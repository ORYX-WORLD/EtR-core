from __future__ import annotations

import json
import math
import os
import signal
import subprocess
import time
import wave
from array import array
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

STATE_FILE = Path(os.getenv("ETR_AUDIO_STATE_FILE", "/var/lib/etr-core/audio-status.json"))
SAMPLE_FILE = Path(os.getenv("ETR_AUDIO_SAMPLE_FILE", "/run/etr-core/microphone-latest.wav"))
SAMPLE_RATE = int(os.getenv("ETR_AUDIO_SAMPLE_RATE", "16000"))
CHANNELS = 1
SAMPLE_WIDTH = 2
CHUNK_SECONDS = 0.1
ROLLING_SECONDS = 5
SPEECH_THRESHOLD_DBFS = float(os.getenv("ETR_AUDIO_SPEECH_THRESHOLD_DBFS", "-42"))
STOP = False


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def atomic_json(payload: dict[str, Any]) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    temp = STATE_FILE.with_suffix(".tmp")
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.chmod(temp, 0o600)
    temp.replace(STATE_FILE)


def dbfs(value: float) -> float:
    if value <= 0:
        return -96.0
    return max(-96.0, round(20.0 * math.log10(value / 32768.0), 1))


def pactl(*args: str) -> str:
    completed = subprocess.run(
        ["/usr/bin/pactl", *args],
        text=True,
        capture_output=True,
        check=True,
        timeout=10,
    )
    return completed.stdout.strip()


def select_source() -> str:
    configured = os.getenv("ETR_AUDIO_SOURCE", "").strip()
    if configured:
        return configured
    try:
        default = pactl("get-default-source")
        if default and not default.endswith(".monitor"):
            return default
    except (OSError, subprocess.SubprocessError):
        pass

    listing = pactl("list", "short", "sources")
    candidates: list[str] = []
    for line in listing.splitlines():
        fields = line.split("\t")
        if len(fields) < 2:
            fields = line.split()
        if len(fields) >= 2 and not fields[1].endswith(".monitor"):
            candidates.append(fields[1])
    if not candidates:
        raise RuntimeError("Aucune source microphone PipeWire/PulseAudio détectée")
    usb = [name for name in candidates if ".usb-" in name or "usb" in name.lower()]
    return (usb or candidates)[0]


def write_wave(chunks: deque[bytes]) -> None:
    SAMPLE_FILE.parent.mkdir(parents=True, exist_ok=True)
    temp = SAMPLE_FILE.with_suffix(".tmp.wav")
    with wave.open(str(temp), "wb") as output:
        output.setnchannels(CHANNELS)
        output.setsampwidth(SAMPLE_WIDTH)
        output.setframerate(SAMPLE_RATE)
        for chunk in chunks:
            output.writeframesraw(chunk)
    os.chmod(temp, 0o600)
    temp.replace(SAMPLE_FILE)


def signal_handler(_signum: int, _frame: object) -> None:
    global STOP
    STOP = True


def offline(error: str, source: str | None = None) -> None:
    atomic_json(
        {
            "schema_version": "1.0",
            "updated_at": utc_now(),
            "online": False,
            "source": source,
            "sample_rate_hz": SAMPLE_RATE,
            "channels": CHANNELS,
            "speech_active": False,
            "error": error[:300],
        }
    )


def monitor_once() -> None:
    source = select_source()
    command = [
        "/usr/bin/parec",
        "--device",
        source,
        "--format=s16le",
        f"--rate={SAMPLE_RATE}",
        f"--channels={CHANNELS}",
        "--raw",
    ]
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if process.stdout is None:
        raise RuntimeError("Flux microphone indisponible")

    chunk_size = int(SAMPLE_RATE * CHANNELS * SAMPLE_WIDTH * CHUNK_SECONDS)
    rolling_chunks = int(ROLLING_SECONDS / CHUNK_SECONDS)
    rolling: deque[bytes] = deque(maxlen=rolling_chunks)
    rms_values: list[float] = []
    peak_value = 0
    last_publish = time.monotonic()
    last_wave = time.monotonic()

    try:
        while not STOP:
            data = process.stdout.read(chunk_size)
            if not data:
                detail = process.stderr.read().decode("utf-8", errors="replace")[-300:] if process.stderr else ""
                raise RuntimeError(detail or "Le flux microphone s'est interrompu")
            rolling.append(data)
            samples = array("h")
            samples.frombytes(data)
            if not samples:
                continue
            square_sum = sum(sample * sample for sample in samples)
            rms_values.append(math.sqrt(square_sum / len(samples)))
            peak_value = max(peak_value, max(abs(sample) for sample in samples))

            now = time.monotonic()
            if now - last_publish >= 1.0:
                rms = sum(rms_values) / max(1, len(rms_values))
                rms_level = dbfs(rms)
                peak_level = dbfs(float(peak_value))
                atomic_json(
                    {
                        "schema_version": "1.0",
                        "updated_at": utc_now(),
                        "online": True,
                        "source": source,
                        "sample_rate_hz": SAMPLE_RATE,
                        "channels": CHANNELS,
                        "rms_dbfs": rms_level,
                        "peak_dbfs": peak_level,
                        "speech_active": rms_level >= SPEECH_THRESHOLD_DBFS,
                        "speech_threshold_dbfs": SPEECH_THRESHOLD_DBFS,
                        "rolling_sample_seconds": ROLLING_SECONDS,
                        "sample_available": SAMPLE_FILE.exists(),
                        "error": None,
                    }
                )
                rms_values.clear()
                peak_value = 0
                last_publish = now
            if len(rolling) == rolling.maxlen and now - last_wave >= ROLLING_SECONDS:
                write_wave(rolling)
                last_wave = now
    finally:
        process.terminate()
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill()


def main() -> int:
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
    while not STOP:
        source: str | None = None
        try:
            source = select_source()
            monitor_once()
        except Exception as exc:  # service long-running: publish and retry
            offline(str(exc), source)
            if not STOP:
                time.sleep(5)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
