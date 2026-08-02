import unittest

from src.ads1263 import ADS1263


class FakeSpi:
    def __init__(self, responses=None):
        self.responses = list(responses or [])
        self.transfers = []
        self.no_cs = None

    def xfer2(self, values):
        self.transfers.append(list(values))
        if self.responses:
            return self.responses.pop(0)
        return [0] * len(values)


class ADS1263MathTests(unittest.TestCase):
    def test_positive_full_scale_ratio(self):
        self.assertEqual(ADS1263.raw_to_ratio(0x7FFFFFFF), 1.0)

    def test_zero_ratio(self):
        self.assertEqual(ADS1263.raw_to_ratio(0), 0.0)

    def test_negative_two_complement_ratio(self):
        self.assertLess(ADS1263.raw_to_ratio(0xFFFFFFFF), 0.0)

    def test_waveshare_checksum_rule(self):
        raw = 0x12345678
        total = sum(raw.to_bytes(4, "big")) + 0x9B
        checksum = total & 0xFF
        self.assertTrue(ADS1263.checksum_valid(raw, checksum))
        self.assertFalse(ADS1263.checksum_valid(raw, checksum ^ 0x01))


class ADS1263SharedBusTests(unittest.TestCase):
    def test_shared_screen_defaults_use_kernel_cs_and_status_polling(self):
        adc = ADS1263()
        self.assertEqual(adc.bus, 0)
        self.assertEqual(adc.device, 2)
        self.assertFalse(adc.manual_chip_select)
        self.assertFalse(adc.use_data_ready_gpio)
        self.assertTrue(adc.use_hardware_reset_gpio)

    def test_register_read_keeps_kernel_chip_select_in_one_transfer(self):
        spi = FakeSpi([[0x00, 0x00, 0x20]])
        adc = ADS1263(use_hardware_reset_gpio=False)
        adc._spi = spi
        value = adc.read_register(adc.REG_ID)
        self.assertEqual(value, 0x20)
        self.assertEqual(spi.transfers, [[adc.CMD_RREG | adc.REG_ID, 0x00, 0x00]])

    def test_conversion_read_does_not_require_gpio17(self):
        raw = 0x12345678
        checksum = (sum(raw.to_bytes(4, "big")) + 0x9B) & 0xFF
        response = [0x00, 0x40, 0x12, 0x34, 0x56, 0x78, checksum]
        spi = FakeSpi([response])
        adc = ADS1263(use_hardware_reset_gpio=False, use_data_ready_gpio=False)
        adc._spi = spi
        self.assertEqual(adc.read_raw(), raw)
        self.assertEqual(spi.transfers, [[adc.CMD_RDATA1, 0, 0, 0, 0, 0, 0]])


if __name__ == "__main__":
    unittest.main()
