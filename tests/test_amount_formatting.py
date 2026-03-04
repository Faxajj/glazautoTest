import unittest

from app.main import _format_amount, _parse_amount_input


class AmountFormattingTests(unittest.TestCase):
    def test_format_amount_supports_eu_style_string(self):
        self.assertEqual(_format_amount("1.234,56"), "1.234,56")

    def test_format_amount_supports_us_style_string(self):
        self.assertEqual(_format_amount("1,234.56"), "1.234,56")

    def test_format_amount_trims_zeroes(self):
        self.assertEqual(_format_amount("130805.50"), "130.805,5")

    def test_parse_amount_supports_eu_and_us(self):
        self.assertEqual(str(_parse_amount_input("1.234,56")), "1234.56")
        self.assertEqual(str(_parse_amount_input("1,234.56")), "1234.56")


if __name__ == "__main__":
    unittest.main()
