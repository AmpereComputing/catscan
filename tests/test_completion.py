# Copyright (c) 2024-2025 Ampere Computing. All rights reserved.
# SPDX-License-Identifier: BSD-3-Clause

import unittest

from catscan.completion import HistoricalCompletion, KeywordCompletion


class TestHistoricalCompletion(unittest.TestCase):
    def setUp(self, original=""):
        self.original = original
        self.history = HistoricalCompletion()


class TestEmptyHistory(TestHistoricalCompletion):
    def test_forward(self):
        self.assertIsNone(self.history.forward(), None)

    def test_back_and_forward(self):
        self.assertEqual(self.history.back(self.original), self.original)
        self.assertEqual(self.history.forward(), self.original)

    def test_back_and_forward_with_position(self):
        self.assertEqual(self.history.back_with_position(self.original, 0), (self.original, 0))
        self.assertEqual(self.history.forward_with_position(), (self.original, 0))


class TestSomeHistory(TestHistoricalCompletion):
    def setUp(self, original=""):
        super().setUp(original=original)
        self.items = ["three", "two", "one"]
        for item in self.items:
            self.history.add(item)
        self.history.reset()


class TestHistory(TestSomeHistory):
    def test_forward(self):
        self.assertIsNone(self.history.forward(), None)

    def test_back(self):
        for item in reversed(self.items):
            self.assertEqual(self.history.back(self.original), item)
        self.assertEqual(self.history.back(self.original), self.items[0])

    def test_back_and_forward(self):
        for _item in self.items:
            self.history.back(self.original)

        for item in self.items[1:]:
            self.assertEqual(self.history.forward(), item)

        self.assertEqual(self.history.forward(), self.original)


class TestHistoryFiltering(TestSomeHistory):
    def setUp(self):
        super().setUp(original="t")

    def test_back(self):
        self.assertEqual(self.history.back(self.original), "two")
        self.assertEqual(self.history.back(self.original), "three")


class TestHistoryRollover(TestHistoricalCompletion):
    def setUp(self):
        super().setUp()
        for idx in range(HistoricalCompletion.MAX_HISTORY * 2):
            self.history.add(str(idx))
        self.history.reset()

    def test_rollover(self):
        for idx in reversed(range(HistoricalCompletion.MAX_HISTORY, HistoricalCompletion.MAX_HISTORY * 2)):
            self.assertEqual(self.history.back(self.original), str(idx))


class TestKeywordCompletion(unittest.TestCase):
    def setUp(self):
        self.completion = KeywordCompletion(["one", "two", "three"])
        self.original = None

    def test_all_suggestions(self):
        self.assertEqual(self.completion.back(""), "one")
        self.assertEqual(self.completion.back(""), "two")
        self.assertEqual(self.completion.back(""), "three")

    def test_single_suggestion(self):
        self.original = "o"
        self.assertEqual(self.completion.back(self.original), "one")
        self.assertEqual(self.completion.back(self.original), "one")

    def test_multiple_suggestion(self):
        self.original = "t"
        self.assertEqual(self.completion.back(self.original), "two")
        self.assertEqual(self.completion.back(self.original), "three")
        self.assertEqual(self.completion.back(self.original), "three")

    def test_inline_all_suggestions(self):
        self.original = " suffix"
        self.assertEqual(self.completion.back_with_position(self.original, 0), ("one suffix", 3))
        self.assertEqual(self.completion.back_with_position(self.original, 3), ("two suffix", 3))
        self.assertEqual(self.completion.back_with_position(self.original, 3), ("three suffix", 5))

    def test_inline_suggestion(self):
        self.original = "o suffix"
        self.assertEqual(
            self.completion.back_with_position(self.original, 1),
            ("one suffix", 3),
        )

    def test_inline_multiple_suggestion(self):
        self.original = "t suffix"
        self.assertEqual(
            self.completion.back_with_position(self.original, 1),
            ("two suffix", 3),
        )
        self.assertEqual(
            self.completion.back_with_position(self.original, 3),
            ("three suffix", 5),
        )
