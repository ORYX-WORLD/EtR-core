#!/usr/bin/env python3
"""Acquire EtR laboratory sensors through the Waveshare ADS1263 HAT."""
from __future__ import annotations

import argparse
import json
import math
import os
import signal
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

try:
    from src.ads1263 import ADS1263, ADS1263Error
except ModuleNotFoundError:  # Direct execution from src/ on the Raspberry Pi.
    from ads1263 import ADS1263, ADS1263Error  # type: ignore

DEFAULT_CONFIG = "/etc/etr-core/sensors.json"
DEFAULT_STATE = "/var/lib/etr-core/telemetry.json"
ACQUISITION_VERSION = "1.0.0"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def clamp(value: float, minimum: float, maximum: float) -> float:
    return min(maximum, max(minimum, value))


def pressure_from_ratio(
    ratio: float,
    *,
    minimum_bar: float = 0.0,
    maximum_bar: float = 45.0,
    minimum_ratio: float = 0.1,
    maximum_ratio: float = 0.9,
) -> float:
    if maximum_ratio <= minimum_ratio:
        raise ValueError("maximum_ratio must be greater than minimum_ratio")
    scaled = (float(ratio) - minimum_ratio) / (maximum_ratio - minimum_ratio)
    return minimum_bar + scaled * (maximum_bar - minimum_bar)


def ntc_resistance_from_ratio(ratio: float, reference_resistor_ohm: float) -> float:
    """Return NTC resistance for: AVDD -> Rref -> AIN -> NTC -> COM."""
    ratio = float(ratio)
    if not 0.0 < ratio < 1.0:
        raise ValueError("NTC divider ratio must be strictly between 0 and 1")
    if reference_resistor_ohm <= 0:
        raise ValueError("reference resistor must be positive")
    return float(reference_resistor_ohm) * ratio / (1.0 - ratio)


def ntc_temperature_beta(
    resistance_ohm: float,
    *,
    nominal_resistance_ohm: float,
    nominal_temperature_c: float,
    beta_k: float,
) -> float:
    if min(resistance_ohm, nominal_resistance_ohm, beta_k) <= 0:
        raise ValueError("NTC parameters must be positive")
    t0 = float(nominal_temperature_c) + 273.15
    reciprocal = (1.0 / t0) + math.log(resistance_ohm / nominal_resistance_ohm) / beta_k
    return (1.0 / reciprocal) - 273.15


def rounded(value: float | None, digits: int = 3) -> float | None:
    return None if value is None or not math.isfinite(value) else round(float(value), digits)


def pressure_sample(channel: dict[str, Any], ratio: float, reference_voltage: float) -> dict[str, Any]:
    signal_voltage = ratio * reference_voltage
    minimum_ratio = float(channel.get("minimum_ratio", 0.1))
    maximum_ratio = float(channel.get("maximum_ratio", 0.9))
    tolerance = float(channel.get("signal_tolerance_ratio", 0.035))
    status = "ok"
    message = "Signal ratiométrique valide"
    if ratio <= max(0.01, minimum_ratio - tolerance):
        status = "signal_low"
        message = "Signal trop bas : vérifier alimentation, masse et fil signal"
    elif ratio >= min(0.99, maximum_ratio + tolerance):
        status = "signal_high"
        message = "Signal trop haut : capteur débranché ou fil signal à contrôler"

    raw_pressure = pressure_from_ratio(
        ratio,
        minimum_bar=float(channel.get("minimum_bar", 0.0)),
        maximum_bar=float(channel.get("maximum_bar", 45.0)),
        minimum_ratio=minimum_ratio,
        maximum_ratio=maximum_ratio,
    )
    minimum_bar = float(channel.get("minimum_bar", 0.0))
    maximum_bar = float(channel.get("maximum_bar", 45.0))
    pressure = clamp(raw_pressure, minimum_bar, maximum_bar) if status == "ok" else None
    return {
        "id": str(channel["id"]),
        "label": str(channel.get("label") or channel["id"]),
        "ain": int(channel["ain"]),
        "kind": "pressure",
        "model": str(channel.get("model") or "CAREL ratiométrique"),
        "status": status,
        "message": message,
        "ratio": rounded(ratio, 6),
        "signal_v": rounded(signal_voltage, 4),
        "value": rounded(pressure, 2),
        "unit": "bar",
        "expected": "≈ 0,5 V à 0 bar ; ≈ 4,5 V à 45 bar",
    }


def ntc_sample(channel: dict[str, Any], ratio: float, reference_voltage: float) -> dict[str, Any]:
    signal_voltage = ratio * reference_voltage
    result: dict[str, Any] = {
        "id": str(channel["id"]),
        "label": str(channel.get("label") or channel["id"]),
        "ain": int(channel["ain"]),
        "kind": "temperature",
        "model": str(channel.get("model") or "AKO NTC"),
        "ratio": rounded(ratio, 6),
        "signal_v": rounded(signal_voltage, 4),
        "value": None,
        "unit": "°C",
        "resistance_ohm": None,
        "expected": "Résistance fixe 10 kΩ entre AVDD et l’entrée AIN",
    }

    if ratio >= float(channel.get("high_ratio_fault", 0.97)):
        result.update(
            status="reference_resistor_missing_or_probe_open",
            message="Tension brute consultable ; température indisponible sans résistance 10 kΩ",
        )
        return result
    if ratio <= float(channel.get("low_ratio_fault", 0.01)):
        result.update(status="short_circuit", message="Tension presque nulle : vérifier un court-circuit vers COM")
        return result

    reference_resistor = float(channel.get("reference_resistor_ohm", 10_000.0))
    resistance = ntc_resistance_from_ratio(ratio, reference_resistor)
    result["resistance_ohm"] = rounded(resistance, 1)
    beta = channel.get("beta_k")
    if beta in (None, "", 0, 0.0):
        result.update(
            status="curve_required",
            message="Pont NTC mesurable ; courbe AKO à valider avant d’afficher une température",
        )
        return result

    temperature = ntc_temperature_beta(
        resistance,
        nominal_resistance_ohm=float(channel.get("nominal_resistance_ohm", 10_000.0)),
        nominal_temperature_c=float(channel.get("nominal_temperature_c", 25.0)),
        beta_k=float(beta),
    )
    minimum_c = float(channel.get("minimum_c", -50.0))
    maximum_c = float(channel.get("maximum_c", 105.0))
    if not minimum_c - 5.0 <= temperature <= maximum_c + 5.0:
        result.update(status="out_of_range", message="Résistance mesurée hors de la plage configurée")
        return result
    result.update(status="ok", message="Sonde NTC mesurée", value=rounded(temperature, 2))
    return result


def build_payload(
    config: dict[str, Any],
    ratios: dict[int, float],
    *,
    chip_id: int,
    now: str | None = None,
) -> dict[str, Any]:
    adc_config = config.get("adc") if isinstance(config.get("adc"), dict) else {}
    reference_voltage = float(adc_config.get("reference_voltage_v", 5.0))
    channels = config.get("channels") if isinstance(config.get("channels"), list) else []
    sensors: list[dict[str, Any]] = []
    measurements: dict[str, float] = {}
    states: dict[str, Any] = {
        "adc_online": True,
        "adc_chip_id": int(chip_id),
        "wiring_profile": str(config.get("profile") or "unknown"),
    }
    alerts: list[dict[str, str]] = []

    for channel in channels:
        if not isinstance(channel, dict) or "id" not in channel or "ain" not in channel:
            continue
        ain = int(channel["ain"])
        if ain not in ratios:
            continue
        ratio = float(ratios[ain])
        kind = str(channel.get("kind") or "").lower()
        if kind == "pressure":
            sample = pressure_sample(channel, ratio, reference_voltage)
            if sample["value"] is not None:
                measurements[f"{sample['id']}_bar"] = float(sample["value"])
            measurements[f"{sample['id']}_signal_v"] = float(sample["signal_v"])
        elif kind in {"ntc", "temperature"}:
            sample = ntc_sample(channel, ratio, reference_voltage)
            if sample["value"] is not None:
                measurements[f"{sample['id']}_c"] = float(sample["value"])
            if sample["resistance_ohm"] is not None:
                measurements[f"{sample['id']}_ohm"] = float(sample["resistance_ohm"])
            measurements[f"{sample['id']}_signal_v"] = float(sample["signal_v"])
        else:
            continue
        sensors.append(sample)
        states[f"{sample['id']}_status"] = sample["status"]
        if sample["status"] not in {"ok", "curve_required", "reference_resistor_missing_or_probe_open"}:
            alerts.append(
                {
                    "code": f"SENSOR_{sample['id'].upper()}_{str(sample['status']).upper()}",
                    "severity": "warning",
                    "message": str(sample["message"]),
                }
            )

    return {
        "schema_version": "1.1",
        "updated_at": now or utc_now(),
        "source": "ads1263-home-lab",
        "acquisition_version": ACQUISITION_VERSION,
        "hardware": {
            "adc": "ADS1263",
            "hat": "Waveshare High-Precision AD HAT",
            "status": "online",
            "chip_id": int(chip_id),
            "mode": "single_ended_aincom",
            "reference": "AVDD_AVSS",
            "reference_voltage_v": reference_voltage,
        },
        "sensors": sensors,
        "measurements": measurements,
        "states": states,
        "alerts": alerts,
    }


def failure_payload(message: str, *, now: str | None = None) -> dict[str, Any]:
    return {
        "schema_version": "1.1",
        "updated_at": now or utc_now(),
        "source": "ads1263-home-lab",
        "acquisition_version": ACQUISITION_VERSION,
        "hardware": {
            "adc": "ADS1263",
            "hat": "Waveshare High-Precision AD HAT",
            "status": "offline",
            "chip_id": None,
            "mode": "single_ended_aincom",
            "reference": "AVDD_AVSS",
        },
        "sensors": [],
        "measurements": {},
        "states": {"adc_online": False},
        "alerts": [{"code": "ADC_UNAVAILABLE", "severity": "critical", "message": str(message)[:240]}],
    }


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=False, separators=(",", ":"))
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary_name, 0o600)
        os.replace(temporary_name, path)
    finally:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass


def load_config(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("sensor configuration must be a JSON object")
    channels = data.get("channels")
    if not isinstance(channels, list) or not channels:
        raise ValueError("sensor configuration requires at least one channel")
    ids: set[str] = set()
    ains: set[int] = set()
    for channel in channels:
        if not isinstance(channel, dict):
            raise ValueError("each sensor channel must be an object")
        identifier = str(channel.get("id") or "").strip()
        ain = int(channel.get("ain", -1))
        if not identifier or identifier in ids:
            raise ValueError("sensor channel IDs must be unique and non-empty")
        if not 0 <= ain <= 9 or ain in ains:
            raise ValueError("sensor AIN channels must be unique and between 0 and 9")
        ids.add(identifier)
        ains.add(ain)
    return data


def acquire_once(config: dict[str, Any], adc_factory: Callable[..., ADS1263] = ADS1263) -> dict[str, Any]:
    adc_config = config.get("adc") if isinstance(config.get("adc"), dict) else {}
    channels = [channel for channel in config.get("channels", []) if isinstance(channel, dict)]
    adc = adc_factory(
        bus=int(adc_config.get("bus", 0)),
        device=int(adc_config.get("device", 0)),
        speed_hz=int(adc_config.get("speed_hz", 2_000_000)),
        gpio_chip=int(adc_config.get("gpio_chip", 0)),
        drdy_timeout_seconds=float(adc_config.get("drdy_timeout_seconds", 1.5)),
    )
    with adc:
        ratios = {
            int(channel["ain"]): adc.read_channel_ratio(
                int(channel["ain"]),
                samples=int(adc_config.get("samples_per_channel", 5)),
                discard=int(adc_config.get("discard_after_switch", 1)),
            )
            for channel in channels
        }
        return build_payload(config, ratios, chip_id=int(adc.chip_id or 0))


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="EtR ADS1263 sensor acquisition")
    parser.add_argument("--config", default=DEFAULT_CONFIG)
    parser.add_argument("--state", default=DEFAULT_STATE)
    parser.add_argument("--once", action="store_true", help="Acquire one frame then exit")
    parser.add_argument("--strict", action="store_true", help="Return a non-zero code when ADC access fails")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    config_path = Path(args.config)
    state_path = Path(args.state)
    try:
        config = load_config(config_path)
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as error:
        print(f"Invalid sensor configuration: {error}", file=sys.stderr)
        return 2

    interval = max(1.0, float(config.get("sample_interval_seconds", 5.0)))
    stopped = False

    def stop(_signum: int, _frame: Any) -> None:
        nonlocal stopped
        stopped = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)

    while not stopped:
        try:
            payload = acquire_once(config)
            atomic_write_json(state_path, payload)
            if args.once:
                print(json.dumps(payload, ensure_ascii=False))
                return 0
        except (ADS1263Error, OSError, RuntimeError, ValueError) as error:
            payload = failure_payload(f"{type(error).__name__}: {error}")
            atomic_write_json(state_path, payload)
            print(payload["alerts"][0]["message"], file=sys.stderr)
            if args.once:
                print(json.dumps(payload, ensure_ascii=False))
                return 3 if args.strict else 0
        deadline = time.monotonic() + interval
        while not stopped and time.monotonic() < deadline:
            time.sleep(min(0.25, max(0.0, deadline - time.monotonic())))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
