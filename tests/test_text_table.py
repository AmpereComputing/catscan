# Copyright (c) 2024-2025 Ampere Computing. All rights reserved.
# SPDX-License-Identifier: BSD-3-Clause

import unittest

from catscan.widgets.text_table import TextTable


class TestTextTable(unittest.TestCase):
    def setUp(self):
        self.header = ["", "a", "b", "c"]
        self.contents = [["first", "1", "2", "3"], ["second", "44", "55", "66"], ["third", "777", "888", "999"]]
        self.footer = ["totals", "822", "945", "1068"]
        self.title = "something made up"

        self.table = TextTable(header=self.header, contents=self.contents, footer=self.footer, title=self.title)

    def test_column_widths(self):
        # plenty of extra width
        widths = self.table._get_column_widths(50)
        self.assertEqual(len(widths), 4)
        self.assertEqual(widths[0], 6)
        self.assertEqual(widths[1], 3)
        self.assertEqual(widths[2], 3)
        self.assertEqual(widths[3], 4)

        # constrained width
        widths = self.table._get_column_widths(20)
        self.assertEqual(len(widths), 4)
        self.assertEqual(widths[0], 5)
        self.assertEqual(widths[1], 2)
        self.assertEqual(widths[2], 2)
        self.assertEqual(widths[3], 2)

    def test_render_title(self):
        text, _ = self.table._render_table(30)
        decoded_text = [r.decode() for r in text]
        self.assertEqual(decoded_text[0], f"{self.title:^30}")
        self.assertEqual(decoded_text[1], " " * 30)

    def test_render_row_length(self):
        text, _ = self.table._render_table(50)
        decoded_text = [r.decode() for r in text]
        for row in decoded_text:
            self.assertEqual(len(row), 50)

    def assertContainsInOrder(self, teststr: str, contents: list[str]):
        start = 0
        for sub in contents:
            pos = teststr.find(sub, start)
            self.assertTrue(pos > -1)
            start = pos + len(sub)

    def test_render_row_contents(self):
        text, _ = self.table._render_table(30)
        decoded_text = [r.decode() for r in text]

        self.assertContainsInOrder(decoded_text[2], self.header)
        self.assertContainsInOrder(decoded_text[4], self.contents[0])
        self.assertContainsInOrder(decoded_text[5], self.contents[1])
        self.assertContainsInOrder(decoded_text[6], self.contents[2])
        self.assertContainsInOrder(decoded_text[8], self.footer)
