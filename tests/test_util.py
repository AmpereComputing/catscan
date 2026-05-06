# Copyright (c) 2024-2025 Ampere Computing. All rights reserved.
# SPDX-License-Identifier: BSD-3-Clause

import re
import unittest

from catscan.util import *


class TestUtil(unittest.TestCase):
    def test_even_odd(self):
        args_expectations = [
            ((0,), "even"),
            ((1,), "odd"),
            ((79,), "odd"),
            ((80,), "even"),
        ]
        for args, expectation in args_expectations:
            self.assertEqual(even_odd(*args), expectation)

    def test_even_odd_focused(self):
        args_expectations = [
            ((0, False), "even"),
            ((1, False), "odd"),
            ((79, False), "odd"),
            ((80, False), "even"),
            ((0, True), "focused"),
            ((1, True), "focused"),
            ((79, True), "focused"),
            ((80, True), "focused"),
        ]
        for args, expectation in args_expectations:
            self.assertEqual(even_odd_focused(*args), expectation)

    def test_str_width(self):
        self.assertEqual(str_width("foo"), 3)
        self.assertEqual(str_width("🔍🔍"), 4)

    def test_str_fit_width(self):
        # Test abbreviations working correctly. On right...
        self.assertEqual(str_fit_width("blah", 3), "bl…")
        self.assertEqual(str_fit_width("blah", 2), "b…")
        self.assertEqual(str_fit_width("blah", 1), "b")
        # on left...
        self.assertEqual(str_fit_width("blah", 3, continuation_right=False), "…ah")
        self.assertEqual(str_fit_width("blah", 2, continuation_right=False), "…h")
        self.assertEqual(str_fit_width("blah", 1, continuation_right=False), "h")
        # and with longer continuations
        self.assertEqual(str_fit_width("longer", 5, continuation="..."), "lo...")
        self.assertEqual(str_fit_width("longer", 4, continuation="..."), "l...")
        self.assertEqual(str_fit_width("longer", 5, continuation="...", continuation_right=False), "...er")
        self.assertEqual(str_fit_width("longer", 4, continuation="...", continuation_right=False), "...r")

        # Exact length is left unmodified
        self.assertEqual(str_fit_width("longer", 6, continuation="...", continuation_right=False, pad="<"), "longer")

        # Padding correctly on left, right, center
        self.assertEqual(str_fit_width("longer", 8, pad="<"), "longer  ")
        self.assertEqual(str_fit_width("longer", 8, pad=">"), "  longer")
        self.assertEqual(str_fit_width("longer", 8, pad="^"), " longer ")

        test_str = str_fit_width(
            "golang2-pidigits.87_0.es.xz", 315, continuation="  …", continuation_right=False, pad=">"
        )
        self.assertEqual(len(test_str), 315)
        self.assertEqual(test_str, " " * 288 + "golang2-pidigits.87_0.es.xz")

        test_str = str_fit_width(
            "golang2-pidigits.87_0.es.xz", 20, continuation="  …", continuation_right=False, pad=">"
        )
        self.assertEqual(len(test_str), 20)
        self.assertEqual(test_str, "  …digits.87_0.es.xz")

        # Test double-width unicode characters
        self.assertEqual(str_fit_width("🐈", 3, pad=">"), " 🐈")
        self.assertEqual(str_fit_width("🐈", 2, pad=">"), "🐈")
        self.assertEqual(str_fit_width("🐈🐈", 3), "🐈…")

    def test_hex_args_to_re(self):
        hex_re = hex_args_to_re([r"\S+\.br_pc", "program_counter"])
        self.assertIsNotNone(hex_re.match("program_counter"))
        self.assertIsNotNone(hex_re.match("sys.soc_0.core_0.bpu.br_pc"))
        self.assertIsNone(hex_re.match("foobar"))
        self.assertIsNone(hex_re.match("inum"))

    def test_glob_to_pattern(self):
        inum_re = re.compile(glob_to_pattern("*.inum"))
        self.assertIsNotNone(inum_re.match("sys.soc_0.core_0.gpc.inum"))
        self.assertIsNotNone(inum_re.match("1234.inum"))
        self.assertIsNone(inum_re.match("1234_inum"))
        self.assertIsNone(inum_re.match("inum.*"))

        ixu_sched_re = re.compile(glob_to_pattern("*.ixu.pipe_#.schedule"))
        self.assertIsNotNone(ixu_sched_re.match("sys.soc_0.core_0.ixu.pipe_10.schedule"))
        self.assertIsNotNone(ixu_sched_re.match("core.ixu.pipe_0.schedule"))
        self.assertIsNotNone(ixu_sched_re.match(".ixu.pipe_5.schedule"))
        self.assertIsNone(inum_re.match("core.ixu.pipe_A.schedule"))
        self.assertIsNone(inum_re.match("pipe_1.schedule"))
