# Copyright (c) 2025 Ampere Computing. All rights reserved.
# SPDX-License-Identifier: BSD-3-Clause

import unittest

from catscan.commands import Arg, CommandCompletion, CommandDefinition, parse_command_args


class TestCommandCompletion(unittest.TestCase):
    def setUp(self, cmds=None):
        self.complete = CommandCompletion(cmds or {})


class TestCommandOnlyCompletion(TestCommandCompletion):
    def setUp(self):
        super().setUp(
            {
                "search": CommandDefinition("search", "search for something"),
                "split": CommandDefinition("split", "split windows"),
            },
        )

    def test_back(self):
        self.assertEqual(self.complete.back("s"), "search")
        self.assertEqual(self.complete.back("s"), "split")


class TestPostitionalArgCompletion(TestCommandCompletion):
    def setUp(self):
        super().setUp(
            {
                "search": CommandDefinition(
                    "search",
                    "search for something",
                    args=(
                        Arg("value"),
                        Arg("type", choices=["first", "last", "lower"]),
                    ),
                )
            },
        )

    def test_nothing(self):
        self.assertEqual(self.complete.back("search "), "search ")

    def test_choices(self):
        self.assertEqual(self.complete.back("search something l"), "search something last")
        self.assertEqual(self.complete.back("search something l"), "search something lower")


class TestKeywordArgCompletion(TestCommandCompletion):
    def setUp(self):
        super().setUp(
            {
                "clear": CommandDefinition(
                    "clear",
                    "clear something",
                    kwargs=(
                        Arg.Optional("search"),
                        Arg.Optional("highlights"),
                    ),
                )
            },
        )

    def test_back_forwards(self):
        self.assertEqual(self.complete.back("clear "), "clear search")
        self.assertEqual(self.complete.forward(), "clear ")

    def test_all_keywords(self):
        self.assertEqual(self.complete.back("clear "), "clear search")
        self.assertEqual(self.complete.back("clear "), "clear highlights")

    def test_keyword(self):
        self.assertEqual(self.complete.back("clear h"), "clear highlights")


class TestBothArgCompletion(TestCommandCompletion):
    def setUp(self):
        super().setUp(
            {
                "search": CommandDefinition(
                    "search",
                    "search for something",
                    args=(Arg("value"),),
                    kwargs=(
                        Arg("trim", types=bool),
                        Arg("type", choices=["first", "last", "lower"]),
                        Arg.Optional("mark", aliases=["bookmark"]),
                    ),
                )
            },
        )

    def test_back_forwards(self):
        self.assertEqual(self.complete.back("search "), "search trim")
        self.assertEqual(self.complete.forward(), "search ")

    def test_all_keywords(self):
        self.assertEqual(self.complete.back("search "), "search trim")
        self.assertEqual(self.complete.back("search "), "search type")
        self.assertEqual(self.complete.back("search "), "search mark")

    def test_all_keywords_quoted(self):
        self.assertEqual(self.complete.back('search "value" '), 'search "value" trim')
        self.assertEqual(self.complete.back('search "value" '), 'search "value" type')
        self.assertEqual(self.complete.back('search "value" '), 'search "value" mark')

    def test_keyword(self):
        self.assertEqual(self.complete.back("search t"), "search trim")
        self.assertEqual(self.complete.back("search t"), "search type")

    def test_keyword_value(self):
        self.assertEqual(self.complete.back("search type=l"), "search type=last")
        self.assertEqual(self.complete.back("search type=l"), "search type=lower")

    def test_keyword_no_value(self):
        self.assertEqual(self.complete.back("search trim=o"), "search trim=o")

    def test_keyword_bool_value(self):
        self.assertEqual(self.complete.back("search trim="), "search trim=yes")
        self.assertEqual(self.complete.back("search trim="), "search trim=no")

    def test_optional_keyword(self):
        self.assertEqual(self.complete.back("search m"), "search mark")

    def test_keyword_alias(self):
        self.assertEqual(self.complete.back("search b"), "search bookmark")


class TestKeywordArgChoicesCompletion(TestCommandCompletion):
    def setUp(self):
        super().setUp(
            {
                "search": CommandDefinition(
                    "search",
                    "search for something",
                    args=(Arg.Required("type", choices=["event", "field", "transaction"]),),
                    kwargs=(Arg.Optional("mark", aliases=["bookmark"]),),
                )
            },
        )

    def test_first(self):
        self.assertEqual(self.complete.back("search "), "search event")
        self.assertEqual(self.complete.back("search "), "search field")
        self.assertEqual(self.complete.back("search "), "search transaction")
        self.assertEqual(self.complete.back("search "), "search transaction")

    def test_first_filtered(self):
        self.assertEqual(self.complete.back("search f"), "search field")

    def test_second(self):
        self.assertEqual(self.complete.back("search event "), "search event mark")


class TestParseCommandArgs(unittest.TestCase):
    def test_none(self):
        self.assertEqual(parse_command_args(""), (None, [], {}))

    def test_command_only(self):
        self.assertEqual(parse_command_args("search"), ("search", [], {}))

    def test_positional_only(self):
        self.assertEqual(parse_command_args("search something else"), ("search", ["something", "else"], {}))

    def test_positional_quoted(self):
        self.assertEqual(
            parse_command_args("search something else 'and more'"),
            ("search", ["something", "else", "and more"], {}),
        )

    def test_mix(self):
        self.assertEqual(
            parse_command_args("search match=lowercase something type=event else"),
            ("search", ["something", "else"], {"match": "lowercase", "type": "event"}),
        )

    def test_multiple(self):
        self.assertEqual(
            parse_command_args("search field=one something field=two else"),
            ("search", ["something", "else"], {"field": ["one", "two"]}),
        )


class TestParseArgsForCommandDefinition(unittest.TestCase):
    def setUp(self, args: list[Arg] | None = None, kwargs: list[Arg] | None = None):
        args = args or []
        kwargs = kwargs or []
        self.command = CommandDefinition(
            "search",
            "search for something",
            args=(
                *args,
                Arg("first"),
                Arg("second", nargs=2),
                Arg("others", nargs="+"),
            ),
            kwargs=(
                *kwargs,
                Arg("trim", types=bool),
                Arg("amount", types=int),
                Arg("type", choices=["first", "last", "lower"]),
                Arg.Optional("mark", aliases=["bookmark"]),
            ),
        )


class TestMostParseArgsForCommandDefinition(TestParseArgsForCommandDefinition):
    def test_positional_only(self):
        self.assertEqual(
            self.command.parse_arguments(["one", "two", "three", "four", "five", "six"], {}),
            (
                ["one", "two", "three", "four", "five", "six"],
                {
                    "first": "one",
                    "second": ["two", "three"],
                    "others": ["four", "five", "six"],
                },
            ),
        )

    def test_bool_keyword(self):
        for value in ("yes", "y", "1", "t"):
            self.assertEqual(self.command.parse_arguments([], {"trim": value}), ([], {"trim": True}))
        for value in ("no", "n", "0", "f"):
            self.assertEqual(self.command.parse_arguments([], {"trim": value}), ([], {"trim": False}))

    def test_integer_keyword(self):
        self.assertEqual(self.command.parse_arguments([], {"amount": "34"}), ([], {"amount": 34}))
        self.assertEqual(self.command.parse_arguments([], {"amount": "0xff"}), ([], {"amount": 0xFF}))

    def test_optional_keyword(self):
        self.assertEqual(self.command.parse_arguments(["mark"], {}), ([], {"mark": True}))

    def test_optional_alias_keyword(self):
        self.assertEqual(self.command.parse_arguments(["bookmark"], {}), ([], {"mark": True}))


class TestVerifyRequiredArg(TestParseArgsForCommandDefinition):
    def setUp(self):
        super().setUp([Arg.Required("something")])

    def test_required(self):
        with self.assertRaises(ValueError):
            self.command.verify({}, 0)

    def test_has_value(self):
        self.command.verify({"something": "one"}, 1)


class TestVerifyRequiredPairOfArgs(TestParseArgsForCommandDefinition):
    def setUp(self):
        super().setUp(
            [
                Arg("start", required_if=1),
                Arg("end", required_if=1),
            ]
        )

    def test_not_required(self):
        self.command.verify({}, 0)

    def test_required(self):
        with self.assertRaises(ValueError):
            self.command.verify({"start": "one"}, 1)

    def test_has_values(self):
        self.command.verify({"start": "one", "end": "two"}, 2)


class TestAllOptional(unittest.TestCase):
    def setUp(self):
        self.command = CommandDefinition(
            "clear",
            "something",
            kwargs=(
                Arg.Optional("start"),
                Arg.Optional("end"),
            ),
        )

    def test_not_required(self):
        self.command.verify({}, 0)

    def test_optional(self):
        self.command.verify({"start": None}, 0)
