import unittest
from unittest import mock

from src.ads1263_probe import (
    CMD_RESET,
    decode_registers,
    hardware_reset,
    probe_attempt,
    read_register_block,
    run_probe,
)


class FakeSpi:
    def __init__(self, responses=None):
        self.responses = list(responses or [])
        self.opened = None
        self.closed = False
        self.max_speed_hz = None
        self.mode = None
        self.bits_per_word = None
        self.lsbfirst = True
        self.cshigh = True
        self.no_cs = True
        self.transfers = []

    def open(self, bus, device):
        self.opened = (bus, device)

    def xfer2(self, values):
        self.transfers.append(list(values))
        if self.responses:
            return self.responses.pop(0)
        return [0] * len(values)

    def close(self):
        self.closed = True


class FakeSpiModule:
    def __init__(self, factory):
        self.factory = factory

    def SpiDev(self):
        return self.factory()


class FakeGpio:
    def __init__(self):
        self.events = []

    def gpiochip_open(self, chip):
        self.events.append(("open", chip))
        return 7

    def gpio_claim_output(self, handle, flags, pin, level):
        self.events.append(("claim", handle, flags, pin, level))

    def gpio_write(self, handle, pin, level):
        self.events.append(("write", handle, pin, level))

    def gpio_free(self, handle, pin):
        self.events.append(("free", handle, pin))

    def gpiochip_close(self, handle):
        self.events.append(("close", handle))


class Ads1263ProbeTests(unittest.TestCase):
    def test_decode_registers_accepts_ads1263_id_and_reset_defaults(self):
        decoded = decode_registers([0x21, 0x11, 0x05])
        self.assertEqual(decoded["chip_id"], 1)
        self.assertEqual(decoded["revision"], 1)
        self.assertTrue(decoded["valid"])
        self.assertTrue(decoded["reset_defaults_match"])

    def test_decode_registers_rejects_a_floating_all_ff_response(self):
        decoded = decode_registers([0xFF, 0xFF, 0xFF])
        self.assertFalse(decoded["valid"])
        self.assertFalse(decoded["reset_defaults_match"])

    def test_read_register_block_keeps_opcodes_and_data_clocks_in_one_transfer(self):
        spi = FakeSpi(responses=[[0, 0, 0x20, 0x11, 0x05]])
        transmit, receive = read_register_block(spi, 0, 3)
        self.assertEqual(transmit, [0x20, 0x02, 0, 0, 0])
        self.assertEqual(receive[-3:], [0x20, 0x11, 0x05])
        self.assertEqual(spi.transfers, [[0x20, 0x02, 0, 0, 0]])

    def test_hardware_reset_pulses_high_low_high_and_releases_gpio(self):
        gpio = FakeGpio()
        result = hardware_reset(gpio, sleep=lambda _seconds: None)
        self.assertTrue(result["ok"])
        writes = [event for event in gpio.events if event[0] == "write"]
        self.assertEqual(writes, [("write", 7, 18, 1), ("write", 7, 18, 0), ("write", 7, 18, 1)])
        self.assertIn(("free", 7, 18), gpio.events)
        self.assertIn(("close", 7), gpio.events)

    def test_probe_attempt_reads_id_power_and_interface(self):
        spi = FakeSpi(
            responses=[
                [0, 0, 0],
                [0],
                [0, 0, 0x22, 0x11, 0x05],
            ]
        )
        result = probe_attempt(
            lambda: spi,
            bus=0,
            device=2,
            mode=1,
            speed_hz=500_000,
            sleep=lambda _seconds: None,
        )
        self.assertTrue(result["ok"])
        self.assertTrue(result["decoded"]["valid"])
        self.assertEqual(result["decoded"]["revision"], 2)
        self.assertEqual(spi.opened, (0, 2))
        self.assertEqual(spi.mode, 1)
        self.assertFalse(spi.no_cs)
        self.assertIn([CMD_RESET], spi.transfers)
        self.assertTrue(spi.closed)

    def test_run_probe_reports_valid_candidate_without_hardware(self):
        created = []

        def factory():
            spi = FakeSpi(
                responses=[
                    [0, 0, 0],
                    [0],
                    [0, 0, 0x20, 0x11, 0x05],
                ]
            )
            created.append(spi)
            return spi

        report = run_probe(
            speeds=[100_000],
            modes=[1],
            spi_module=FakeSpiModule(factory),
            gpio_module=FakeGpio(),
            sleep=lambda _seconds: None,
        )
        self.assertTrue(report["ads1263_detected"])
        self.assertEqual(report["device"], "/dev/spidev0.2")
        self.assertEqual(report["valid_candidates"][0]["mode"], 1)
        self.assertEqual(len(created), 1)

    def test_probe_attempt_preserves_errors_in_report(self):
        spi = FakeSpi()
        spi.open = mock.Mock(side_effect=PermissionError("denied"))
        result = probe_attempt(
            lambda: spi,
            bus=0,
            device=2,
            mode=1,
            speed_hz=100_000,
            sleep=lambda _seconds: None,
        )
        self.assertFalse(result["ok"])
        self.assertIn("PermissionError", result["error"])
        self.assertTrue(spi.closed)


if __name__ == "__main__":
    unittest.main()
