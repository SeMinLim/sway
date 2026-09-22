#!/usr/bin/env python3
"""Native BVI bank-identity validation before the generated-Verilog build."""

import unittest

from run_verilator import check_native_rtl


def fixture(banks):
    instances = "\n".join(f"SwayCoeffRom #(.BANK_ID(32'd{bank})) bank{index}();"
                          for index, bank in enumerate(banks))
    return ('module mkTbSway();\n' + instances +
            '\ninputFixture #(.file("generated/test_input.hex")) inputs();' +
            '\noutputFixture #(.file("generated/test_expected.hex")) outputs();\nendmodule\n')


class TestNativeRTL(unittest.TestCase):
    def setUp(self):
        self.resource_banks = [bank for bank in range(44) if bank // 4 not in (3, 7)]

    def test_default_accepts_all_44_unique_bank_ids(self):
        self.assertEqual(check_native_rtl(fixture(reversed(range(44)))), list(range(44)))

    def test_resource_comparison_accepts_exact_36_bank_ids(self):
        self.assertEqual(check_native_rtl(fixture(self.resource_banks), True), self.resource_banks)

    def test_partial_set_requires_explicit_resource_mode(self):
        with self.assertRaisesRegex(ValueError, "Expected native coefficient BVI bank IDs"):
            check_native_rtl(fixture(self.resource_banks))

    def test_resource_mode_rejects_44_banks(self):
        with self.assertRaisesRegex(ValueError, "Expected native coefficient BVI bank IDs"):
            check_native_rtl(fixture(range(44)), True)

    def test_wrong_identity_with_correct_count_is_rejected(self):
        for replacement in (12, 28, 0):
            with self.subTest(replacement=replacement):
                banks = self.resource_banks[:-1] + [replacement]
                with self.assertRaisesRegex(ValueError, "Expected native coefficient BVI bank IDs"):
                    check_native_rtl(fixture(banks), True)

    def test_missing_or_duplicate_banks_are_rejected(self):
        for banks in (self.resource_banks[:-1], self.resource_banks + [0]):
            with self.subTest(banks=banks):
                with self.assertRaisesRegex(ValueError, "Expected native coefficient BVI bank IDs"):
                    check_native_rtl(fixture(banks), True)

    def test_unexpected_file_dependency_is_rejected(self):
        rtl = fixture(self.resource_banks) + '\nother #(.file("weights.hex")) coefficients();'
        with self.assertRaisesRegex(ValueError, "Unexpected generated-Verilog file dependencies"):
            check_native_rtl(rtl, True)

    def test_wrong_top_module_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "does not declare mkTbSway"):
            check_native_rtl(fixture(self.resource_banks).replace("module mkTbSway", "module Wrong"), True)


if __name__ == "__main__":
    unittest.main()
