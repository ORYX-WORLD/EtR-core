import unittest

from src.ads1263 import ADS1263


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


if __name__ == "__main__":
    unittest.main()
