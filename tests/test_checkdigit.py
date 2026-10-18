"""校验位与格式验证测试（规格 3.2 / 19-A）。"""

import unittest

from client.barcode.checkdigit import (
    BARCODE_LENGTHS,
    BarcodeValidationError,
    calculate_check_digit,
    is_valid,
    normalize,
    validate,
)

VALID = {
    "UPC-A": "036000291452",
    "EAN-13": "5901234123457",
    "EAN-8": "96385074",
}


class TestCheckDigit(unittest.TestCase):
    def test_known_valid_codes(self):
        for btype, value in VALID.items():
            self.assertTrue(is_valid(value, btype), f"{btype} {value} 应通过校验")

    def test_calculate_check_digit(self):
        self.assertEqual(calculate_check_digit("03600029145", "UPC-A"), "2")
        self.assertEqual(calculate_check_digit("590123412345", "EAN-13"), "7")
        self.assertEqual(calculate_check_digit("9638507", "EAN-8"), "4")

    def test_wrong_check_digit_rejected(self):
        self.assertFalse(is_valid("036000291453", "UPC-A"))
        self.assertFalse(is_valid("5901234123458", "EAN-13"))
        self.assertFalse(is_valid("96385075", "EAN-8"))
        with self.assertRaises(BarcodeValidationError):
            validate("5901234123458", "EAN-13")

    def test_wrong_length_rejected(self):
        with self.assertRaises(BarcodeValidationError):
            validate("12345", "EAN-13")
        with self.assertRaises(BarcodeValidationError):
            validate("", "UPC-A")

    def test_non_digit_rejected(self):
        with self.assertRaises(BarcodeValidationError):
            validate("59012341234A7", "EAN-13")
        with self.assertRaises(BarcodeValidationError):
            validate("\uff10\uff13\uff16\uff10\uff10\uff10\uff12\uff19\uff11\uff14\uff15\uff12", "UPC-A")
        with self.assertRaises(BarcodeValidationError):
            validate("590 234123457", "EAN-13")

    def test_unsupported_type_rejected(self):
        with self.assertRaises(BarcodeValidationError):
            validate("123456789012", "CODE-39")

    def test_calculate_partial_length_enforced(self):
        with self.assertRaises(BarcodeValidationError):
            calculate_check_digit("123", "EAN-13")

    def test_normalize_autofill_check_digit(self):
        self.assertEqual(normalize("03600029145", "UPC-A"), "036000291452")
        self.assertEqual(normalize("590123412345", "EAN-13"), "5901234123457")
        self.assertEqual(normalize("9638507", "EAN-8"), "96385074")
        self.assertEqual(normalize("5901234123457", "EAN-13"), "5901234123457")
        with self.assertRaises(BarcodeValidationError):
            normalize("59012341234", "EAN-13")

    def test_all_lengths_defined(self):
        self.assertEqual(BARCODE_LENGTHS, {"UPC-A": 12, "EAN-13": 13, "EAN-8": 8})


if __name__ == "__main__":
    unittest.main()
