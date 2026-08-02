"""ADS1263 driver for the Waveshare High-Precision AD HAT.

The implementation follows the public Waveshare protocol while supporting a
shared SPI0 bus. On the EtR prototype, SPI0.0 and SPI0.1 are occupied by the
`tft35a` display and touch controller; the ADS1263 is exposed as SPI0.2 with
GPIO22 managed by the kernel. GPIO17 is already used by the touchscreen IRQ,
so data-ready can be polled from the ADS1263 status byte instead.
"""
from __future__ import annotations

import statistics
import time
from dataclasses import dataclass
from typing import Any


class ADS1263Error(RuntimeError):
    """Base exception raised by the ADC driver."""


class ADS1263Timeout(ADS1263Error):
    """Raised when the ADC never reports a completed conversion."""


class ADS1263ChecksumError(ADS1263Error):
    """Raised when the ADC checksum does not match."""


@dataclass(frozen=True)
class ADS1263Pins:
    reset: int = 18
    chip_select: int = 22
    data_ready: int = 17


class ADS1263:
    """Read single-ended ADS1263 channels against AINCOM.

    The HAT default jumpers connect AVDD to 5 V, AVSS to GND and COM to GND.
    Ratios are therefore independent of the exact 5 V supply value, which is
    especially useful for ratiometric pressure sensors and NTC dividers.
    """

    REG_ID = 0x00
    REG_MODE0 = 0x03
    REG_MODE1 = 0x04
    REG_MODE2 = 0x05
    REG_INPMUX = 0x06
    REG_REFMUX = 0x0F

    CMD_RESET = 0x06
    CMD_START1 = 0x08
    CMD_STOP1 = 0x0A
    CMD_RDATA1 = 0x12
    CMD_RREG = 0x20
    CMD_WREG = 0x40

    # Waveshare reference values: PGA bypass, gain 1, 50 SPS, AVDD/AVSS ref,
    # 35 us delay and FIR digital filter.
    MODE0_DELAY_35_US = 0x03
    MODE1_FIR = 0x84
    MODE2_PGA_BYPASS_50_SPS = 0x80 | 0x05
    REFMUX_AVDD_AVSS = 0x24
    AINCOM = 0x0A

    FULL_SCALE = 0x7FFFFFFF

    def __init__(
        self,
        *,
        bus: int = 0,
        device: int = 2,
        speed_hz: int = 2_000_000,
        gpio_chip: int = 0,
        pins: ADS1263Pins | None = None,
        drdy_timeout_seconds: float = 1.5,
        manual_chip_select: bool = False,
        use_data_ready_gpio: bool = False,
        use_hardware_reset_gpio: bool = True,
        spi_module: Any | None = None,
        gpio_module: Any | None = None,
    ) -> None:
        self.bus = int(bus)
        self.device = int(device)
        self.speed_hz = int(speed_hz)
        self.gpio_chip = int(gpio_chip)
        self.pins = pins or ADS1263Pins()
        self.drdy_timeout_seconds = float(drdy_timeout_seconds)
        self.manual_chip_select = bool(manual_chip_select)
        self.use_data_ready_gpio = bool(use_data_ready_gpio)
        self.use_hardware_reset_gpio = bool(use_hardware_reset_gpio)
        self._spidev_module = spi_module
        self._lgpio = gpio_module
        self._spi: Any | None = None
        self._gpio_handle: int | None = None
        self._claimed_pins: set[int] = set()
        self._initialized = False
        self.chip_id: int | None = None

    def __enter__(self) -> "ADS1263":
        self.initialize()
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        self.close()

    def _load_modules(self) -> None:
        if self._spidev_module is None:
            try:
                import spidev  # type: ignore
            except ImportError as error:  # pragma: no cover - hardware only
                raise ADS1263Error("python3-spidev is not installed") from error
            self._spidev_module = spidev
        if self._lgpio is None and (
            self.manual_chip_select or self.use_data_ready_gpio or self.use_hardware_reset_gpio
        ):
            try:
                import lgpio  # type: ignore
            except ImportError as error:  # pragma: no cover - hardware only
                raise ADS1263Error("python3-lgpio is not installed") from error
            self._lgpio = lgpio

    def initialize(self) -> int:
        if self._initialized:
            return int(self.chip_id or 0)
        self._load_modules()
        assert self._spidev_module is not None

        try:
            if self._lgpio is not None:
                self._gpio_handle = self._lgpio.gpiochip_open(self.gpio_chip)
                if self.use_hardware_reset_gpio:
                    self._lgpio.gpio_claim_output(self._gpio_handle, 0, self.pins.reset, 1)
                    self._claimed_pins.add(self.pins.reset)
                if self.manual_chip_select:
                    self._lgpio.gpio_claim_output(self._gpio_handle, 0, self.pins.chip_select, 1)
                    self._claimed_pins.add(self.pins.chip_select)
                if self.use_data_ready_gpio:
                    pull_up = int(getattr(self._lgpio, "SET_PULL_UP", 32))
                    self._lgpio.gpio_claim_input(self._gpio_handle, pull_up, self.pins.data_ready)
                    self._claimed_pins.add(self.pins.data_ready)

            self._spi = self._spidev_module.SpiDev()
            self._spi.open(self.bus, self.device)
            self._spi.max_speed_hz = self.speed_hz
            self._spi.mode = 0b01
            self._spi.bits_per_word = 8
            if hasattr(self._spi, "no_cs"):
                self._spi.no_cs = self.manual_chip_select

            if self.use_hardware_reset_gpio:
                self._hardware_reset()
            else:
                self.write_command(self.CMD_RESET)
                time.sleep(0.25)

            chip_id = self.read_chip_id()
            if chip_id != 0x01:
                raise ADS1263Error(f"ADS1263 chip ID invalid: {chip_id}")
            self.chip_id = chip_id
            self.write_command(self.CMD_STOP1)
            self.write_register(self.REG_MODE2, self.MODE2_PGA_BYPASS_50_SPS)
            self.write_register(self.REG_REFMUX, self.REFMUX_AVDD_AVSS)
            self.write_register(self.REG_MODE0, self.MODE0_DELAY_35_US)
            self.write_register(self.REG_MODE1, self.MODE1_FIR)
            self._verify_register(self.REG_MODE2, self.MODE2_PGA_BYPASS_50_SPS)
            self._verify_register(self.REG_REFMUX, self.REFMUX_AVDD_AVSS)
            self._verify_register(self.REG_MODE0, self.MODE0_DELAY_35_US)
            self._verify_register(self.REG_MODE1, self.MODE1_FIR)
            self.write_command(self.CMD_START1)
            self._initialized = True
            return chip_id
        except Exception:
            self.close()
            raise

    def _hardware_reset(self) -> None:
        self._write_gpio(self.pins.reset, 1)
        time.sleep(0.2)
        self._write_gpio(self.pins.reset, 0)
        time.sleep(0.2)
        self._write_gpio(self.pins.reset, 1)
        time.sleep(0.2)

    def _write_gpio(self, pin: int, level: int) -> None:
        if self._gpio_handle is None or self._lgpio is None:
            raise ADS1263Error("GPIO is not initialized")
        self._lgpio.gpio_write(self._gpio_handle, pin, int(level))

    def _read_gpio(self, pin: int) -> int:
        if self._gpio_handle is None or self._lgpio is None:
            raise ADS1263Error("GPIO is not initialized")
        return int(self._lgpio.gpio_read(self._gpio_handle, pin))

    def _transfer(self, values: list[int]) -> list[int]:
        if self._spi is None:
            raise ADS1263Error("SPI is not initialized")
        return [int(value) & 0xFF for value in self._spi.xfer2(values)]

    def _select(self) -> None:
        if self.manual_chip_select:
            self._write_gpio(self.pins.chip_select, 0)

    def _deselect(self) -> None:
        if self.manual_chip_select:
            self._write_gpio(self.pins.chip_select, 1)

    def write_command(self, command: int) -> None:
        self._select()
        try:
            self._transfer([command & 0xFF])
        finally:
            self._deselect()

    def write_register(self, register: int, value: int) -> None:
        self._select()
        try:
            self._transfer([self.CMD_WREG | (register & 0x1F), 0x00, value & 0xFF])
        finally:
            self._deselect()

    def read_register(self, register: int) -> int:
        self._select()
        try:
            response = self._transfer([self.CMD_RREG | (register & 0x1F), 0x00, 0x00])
            return response[2]
        finally:
            self._deselect()

    def _verify_register(self, register: int, expected: int) -> None:
        actual = self.read_register(register)
        if actual != expected:
            raise ADS1263Error(
                f"ADS1263 register 0x{register:02X}: expected 0x{expected:02X}, got 0x{actual:02X}"
            )

    def read_chip_id(self) -> int:
        return self.read_register(self.REG_ID) >> 5

    def set_single_ended_channel(self, channel: int) -> None:
        if not 0 <= int(channel) <= 9:
            raise ValueError("ADS1263 channel must be between AIN0 and AIN9")
        value = (int(channel) << 4) | self.AINCOM
        self.write_register(self.REG_INPMUX, value)
        self._verify_register(self.REG_INPMUX, value)

    def wait_data_ready(self) -> None:
        if not self.use_data_ready_gpio:
            return
        deadline = time.monotonic() + self.drdy_timeout_seconds
        while time.monotonic() < deadline:
            if self._read_gpio(self.pins.data_ready) == 0:
                return
            time.sleep(0.0005)
        raise ADS1263Timeout("ADS1263 DRDY timeout")

    @staticmethod
    def checksum_valid(raw: int, checksum: int) -> bool:
        value = int(raw) & 0xFFFFFFFF
        total = 0
        while value:
            total += value & 0xFF
            value >>= 8
        return (((total + 0x9B) & 0xFF) ^ (checksum & 0xFF)) == 0

    def read_raw(self) -> int:
        self.wait_data_ready()
        deadline = time.monotonic() + self.drdy_timeout_seconds
        while time.monotonic() < deadline:
            self._select()
            try:
                # One SPI message keeps kernel-managed CS asserted across the
                # command, status byte, data bytes and checksum byte.
                response = self._transfer([self.CMD_RDATA1, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00])
            finally:
                self._deselect()
            status = response[1]
            if status & 0x40:
                data = response[2:7]
                raw = (data[0] << 24) | (data[1] << 16) | (data[2] << 8) | data[3]
                if not self.checksum_valid(raw, data[4]):
                    raise ADS1263ChecksumError("ADS1263 data checksum mismatch")
                return raw
            time.sleep(0.001)
        raise ADS1263Timeout("ADS1263 conversion status not ready")

    @classmethod
    def raw_to_ratio(cls, raw: int) -> float:
        value = int(raw) & 0xFFFFFFFF
        if value & 0x80000000:
            signed = value - 0x1_0000_0000
            return max(-1.0, signed / float(cls.FULL_SCALE))
        return min(1.0, value / float(cls.FULL_SCALE))

    def read_channel_ratio(self, channel: int, *, samples: int = 5, discard: int = 1) -> float:
        if samples < 1 or discard < 0:
            raise ValueError("samples must be >= 1 and discard must be >= 0")
        self.set_single_ended_channel(channel)
        for _ in range(discard):
            self.read_raw()
        ratios = [self.raw_to_ratio(self.read_raw()) for _ in range(samples)]
        return float(statistics.median(ratios))

    def close(self) -> None:
        if self._spi is not None:
            try:
                if self._initialized:
                    self.write_command(self.CMD_STOP1)
            except Exception:
                pass
            try:
                self._spi.close()
            except Exception:
                pass
            self._spi = None
        if self._gpio_handle is not None and self._lgpio is not None:
            for pin in tuple(self._claimed_pins):
                try:
                    self._lgpio.gpio_free(self._gpio_handle, pin)
                except Exception:
                    pass
            try:
                self._lgpio.gpiochip_close(self._gpio_handle)
            except Exception:
                pass
            self._gpio_handle = None
        self._claimed_pins.clear()
        self._initialized = False
