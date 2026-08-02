#!/usr/bin/env python3
"""Diagnostic SPI non destructif du HAT Waveshare ADS1263.

Le programme lit uniquement les trois premiers registres après une remise à
zéro. Il n'écrit aucune configuration métier et produit un JSON exploitable par
GitHub Actions. Les valeurs attendues après reset sont documentées par TI :
ID[7:5] = 001b, POWER = 0x11 et INTERFACE = 0x05.
"""

from __future__ import annotations

import argparse
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

PROBE_VERSION = "1.0.0"
CMD_RESET = 0x06
CMD_RREG = 0x20
REG_ID = 0x00
EXPECTED_POWER = 0x11
EXPECTED_INTERFACE = 0x05


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def decode_registers(values: list[int]) -> dict[str, Any]:
    padded = [int(value) & 0xFF for value in values[:3]]
    while len(padded) < 3:
        padded.append(0)
    identifier, power, interface = padded
    chip_id = identifier >> 5
    return {
        "id_raw": identifier,
        "chip_id": chip_id,
        "revision": identifier & 0x1F,
        "power": power,
        "interface": interface,
        "valid": chip_id == 1,
        "reset_defaults_match": chip_id == 1
        and power == EXPECTED_POWER
        and interface == EXPECTED_INTERFACE,
    }


def read_register_block(spi: Any, start: int = REG_ID, count: int = 3) -> tuple[list[int], list[int]]:
    if not 1 <= int(count) <= 32:
        raise ValueError("count must be between 1 and 32")
    transmit = [CMD_RREG | (int(start) & 0x1F), int(count) - 1] + [0x00] * int(count)
    receive = [int(value) & 0xFF for value in spi.xfer2(transmit)]
    if len(receive) != len(transmit):
        raise RuntimeError(f"SPI response length {len(receive)} != {len(transmit)}")
    return transmit, receive


def hardware_reset(
    gpio_module: Any,
    *,
    gpio_chip: int = 0,
    reset_pin: int = 18,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    handle: int | None = None
    try:
        handle = int(gpio_module.gpiochip_open(int(gpio_chip)))
        gpio_module.gpio_claim_output(handle, 0, int(reset_pin), 1)
        gpio_module.gpio_write(handle, int(reset_pin), 1)
        sleep(0.2)
        gpio_module.gpio_write(handle, int(reset_pin), 0)
        sleep(0.2)
        gpio_module.gpio_write(handle, int(reset_pin), 1)
        sleep(0.3)
        return {"ok": True, "gpio_chip": int(gpio_chip), "reset_pin": int(reset_pin)}
    except Exception as error:
        return {
            "ok": False,
            "gpio_chip": int(gpio_chip),
            "reset_pin": int(reset_pin),
            "error": f"{type(error).__name__}: {error}"[:500],
        }
    finally:
        if handle is not None:
            try:
                gpio_module.gpio_free(handle, int(reset_pin))
            except Exception:
                pass
            try:
                gpio_module.gpiochip_close(handle)
            except Exception:
                pass


def probe_attempt(
    spi_factory: Callable[[], Any],
    *,
    bus: int,
    device: int,
    mode: int,
    speed_hz: int,
    software_reset: bool = True,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    spi = spi_factory()
    attempt: dict[str, Any] = {
        "bus": int(bus),
        "device": int(device),
        "mode": int(mode),
        "speed_hz": int(speed_hz),
        "software_reset": bool(software_reset),
    }
    try:
        spi.open(int(bus), int(device))
        spi.max_speed_hz = int(speed_hz)
        spi.mode = int(mode)
        spi.bits_per_word = 8
        if hasattr(spi, "lsbfirst"):
            spi.lsbfirst = False
        if hasattr(spi, "cshigh"):
            spi.cshigh = False
        if hasattr(spi, "no_cs"):
            spi.no_cs = False

        idle_tx = [0x00, 0x00, 0x00]
        attempt["idle_rx"] = [int(value) & 0xFF for value in spi.xfer2(idle_tx)]
        if software_reset:
            attempt["reset_rx"] = [int(value) & 0xFF for value in spi.xfer2([CMD_RESET])]
            sleep(0.3)

        transmit, receive = read_register_block(spi, REG_ID, 3)
        registers = receive[-3:]
        attempt.update(
            ok=True,
            tx=transmit,
            rx=receive,
            registers=registers,
            decoded=decode_registers(registers),
        )
    except Exception as error:
        attempt.update(ok=False, error=f"{type(error).__name__}: {error}"[:500])
    finally:
        try:
            spi.close()
        except Exception:
            pass
    return attempt


def run_probe(
    *,
    bus: int = 0,
    device: int = 2,
    reset_pin: int = 18,
    gpio_chip: int = 0,
    speeds: list[int] | None = None,
    modes: list[int] | None = None,
    spi_module: Any | None = None,
    gpio_module: Any | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    if spi_module is None:
        import spidev as spi_module  # type: ignore
    if gpio_module is None:
        try:
            import lgpio as gpio_module  # type: ignore
        except ImportError:
            gpio_module = None

    selected_speeds = speeds or [2_000_000, 500_000, 100_000, 50_000]
    selected_modes = modes or [1, 0, 2, 3]
    reset_result = (
        hardware_reset(
            gpio_module,
            gpio_chip=gpio_chip,
            reset_pin=reset_pin,
            sleep=sleep,
        )
        if gpio_module is not None
        else {"ok": False, "error": "lgpio unavailable", "gpio_chip": gpio_chip, "reset_pin": reset_pin}
    )

    attempts: list[dict[str, Any]] = []
    for mode in selected_modes:
        for speed in selected_speeds:
            attempts.append(
                probe_attempt(
                    spi_module.SpiDev,
                    bus=bus,
                    device=device,
                    mode=mode,
                    speed_hz=speed,
                    software_reset=True,
                    sleep=sleep,
                )
            )

    valid = [
        {
            "mode": item["mode"],
            "speed_hz": item["speed_hz"],
            **item.get("decoded", {}),
        }
        for item in attempts
        if item.get("decoded", {}).get("valid") is True
    ]
    return {
        "probe_version": PROBE_VERSION,
        "checked_at": utc_now(),
        "device": f"/dev/spidev{int(bus)}.{int(device)}",
        "hardware_reset": reset_result,
        "attempts": attempts,
        "valid_candidates": valid,
        "ads1263_detected": bool(valid),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Probe ADS1263 registers over SPI")
    parser.add_argument("--bus", type=int, default=0)
    parser.add_argument("--device", type=int, default=2)
    parser.add_argument("--gpio-chip", type=int, default=0)
    parser.add_argument("--reset-pin", type=int, default=18)
    parser.add_argument("--output", type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    report = run_probe(
        bus=args.bus,
        device=args.device,
        gpio_chip=args.gpio_chip,
        reset_pin=args.reset_pin,
    )
    serialized = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(serialized, encoding="utf-8")
    print(serialized, end="")
    return 0 if report["ads1263_detected"] else 3


if __name__ == "__main__":
    raise SystemExit(main())
