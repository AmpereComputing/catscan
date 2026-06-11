# Copyright (c) 2024 Ampere Computing. All rights reserved.
# SPDX-License-Identifier: BSD-3-Clause

import re
import shlex
import sys
from collections import defaultdict
from collections.abc import Callable
from enum import StrEnum
from fractions import Fraction
from functools import cached_property
from typing import Any

from catscan.completion import FilteredSuggestions
from catscan.search import MatchType
from catscan.util import glob_to_pattern


# Commands which are explicitly called with ":<command>"
class Commands(StrEnum):
    HELP = "help"
    QUIT = "quit"
    CLEAR = "clear"
    SEARCH = "search"
    SEARCH_ROW = "searchrow"
    SEARCH_FIELD = "searchfield"
    SEARCH_INT = "searchint"
    SUMMARIZE = "summarize"
    SYNC_COMMITS = "sync_commits"
    PIN_ROW = "pin"
    UNPIN_ROW = "unpin"
    ZOOM = "zoom"
    MARKS = "marks"


# Commands which are not explicit (i.e. ":<argument>")
class DefaultCommands(StrEnum):
    GOTO_TIME = "gototime"
    GOTO_EVENT_ROW = "gotoeventrow"


# Type of zoom for ZOOM command
class ZoomTypes(StrEnum):
    FIT = "fit"
    EXTENTS = "extents"
    SEARCH = "search"
    HIGHLIGHTS = "highlights"
    MARKS = "marks"


class Arg:
    """Command argument definition."""

    def __init__(
        self,
        name: str,
        *,
        description: str | None = None,
        default: Any | None = None,
        choices: list | None = None,
        types: Callable | tuple | None = str,
        nargs: int | str = 1,
        required: bool = False,
        required_if: int | None = None,
        aliases: list[str] | None = None,
        multiple: bool = False,
        mutually_exclusive: str | None = None,
    ):
        self.name = name
        self.aliases = [name] + (aliases or [])
        self.description = description
        self._default = default
        self._choices = choices
        self._types = (types or []) if types is None or isinstance(types, tuple) else [types]
        self._multiple = multiple
        if isinstance(nargs, int):
            self.nargs = nargs
        elif nargs == "+":
            self.nargs = sys.maxsize
        self._requires = 0 if required else required_if
        self._mutually_exclusive = mutually_exclusive

    @classmethod
    def Optional(cls, *args: Any, **kwargs: Any) -> "Arg":  # noqa: N802
        return cls(*args, nargs=0, **kwargs)

    @classmethod
    def Required(cls, *args: Any, **kwargs: Any) -> "Arg":  # noqa: N802
        return cls(*args, required=True, **kwargs)

    @property
    def valueless(self) -> bool:
        return self.nargs == 0

    @property
    def required(self) -> bool:
        return self._requires == 0

    @property
    def optional(self) -> bool:
        return self._requires is None

    @property
    def required_at(self) -> int | None:
        return self._requires if self._requires is not None and self._requires > 0 else None

    @property
    def default(self) -> Any:
        return self._default

    def has_default(self) -> bool:
        return self._default is not None

    @property
    def mutually_exclusive(self) -> bool:
        return self._mutually_exclusive is not None

    @property
    def mutually_exclusive_group(self) -> str | None:
        return self._mutually_exclusive

    @property
    def choices(self) -> list[Any]:
        choices = []
        for arg_type in self._types:
            if self._choices is not None:
                choices.extend(self._choices)
            elif arg_type is bool:
                choices.extend(("yes", "no"))
            elif issubclass(arg_type, StrEnum):
                choices.extend([value.lower() for value in arg_type])
        return choices

    def _parse_value(self, value: str) -> Any:
        last_exception = None
        for arg_type in self._types:
            try:
                if arg_type is bool:
                    v = value[0].lower()
                    if v in ("1", "y", "t"):
                        return True
                    if v in ("0", "n", "f"):
                        return False
                    raise ValueError("Invalid boolean value")
                if arg_type is int:
                    return int(value, base=0)

                return arg_type(value)

            except Exception as e:
                last_exception = e

        if last_exception:
            raise last_exception
        raise ValueError("Unknown argument value")

    def parse(self, value: list | str) -> Any:
        if self.valueless:
            return True

        if isinstance(value, list):
            return [self._parse_value(v) for v in value]

        parsed = self._parse_value(value)
        return [parsed] if self._multiple else parsed

    def requirement_met(self, present: bool, nargs: int) -> bool:
        return present or self.optional or self._requires > nargs

    def signature(self, positional: bool = True) -> str:
        if positional:
            name = self.name.replace("_", " ")
            if self.nargs > 3:
                name = f"{name} ..."
            elif self.nargs > 1:
                name = ", ".join(name * self.nargs)

            name = f"<{name}>"
            if self.optional:
                name = f"[{name}]"
        else:
            name = f"[{self.name}"
            if not self.valueless:
                name += "="
                if self.choices and len(self.choices) < 8:
                    name += f"({' | '.join(map(str, self.choices))})"
                else:
                    arg_types = [
                        arg_type.__name__ if isinstance(arg_type, type) else str(arg_type) for arg_type in self._types
                    ]
                    name += f"<{', '.join(arg_types)}>"
            if self._multiple:
                name += "..."
            name += "]"

        return name


class CommandDefinition:
    def __init__(
        self,
        name: str,
        description: str,
        *,
        example: str | None = None,
        display_name: bool = True,
        specification: str | re.Pattern | tuple | None = None,
        args: tuple[Arg, ...] | None = None,
        kwargs: tuple[Arg, ...] | None = None,
    ):
        self.name = name
        self.specification = specification or name
        self.display_name = display_name
        self.description = description
        self._example = example or ""

        self._args = args or []
        if kwargs:
            self._kwargs = {arg.name: arg for arg in kwargs}
        else:
            self._kwargs = {}
        self._kwargs_aliases = {name: arg for arg in self._kwargs.values() for name in arg.aliases}

        self._all_args = {arg.name: arg for arg in self._args}
        self._all_args.update(self._kwargs)

    def matches(self, arg: str) -> tuple[bool, tuple[str, ...]]:
        """If argument matches, providing a tuple of match and parts of argument/command."""
        if isinstance(self.specification, re.Pattern):
            if m := self.specification.match(arg):
                return (True, m.groups())
            return (False, ())
        if isinstance(self.specification, tuple):
            return (arg in self.specification, ())
        return (self.specification == arg, ())

    def parse_arguments(self, args: list[str], kwargs: dict[str, Any]) -> tuple[list[Any], dict[str, Any]]:
        """Parse positional and keyword arguments."""
        mutually_exclusive_groups: dict[str, set[str]] = defaultdict(set)
        positional_args = [
            arg for arg in args if arg not in self._kwargs_aliases or not self._kwargs_aliases[arg].valueless
        ]

        arguments = {name: arg.default for name, arg in self._kwargs.items() if arg.has_default()}
        index = 0
        arg_index = 0
        for word in positional_args:
            try:
                arg = self._args[index]
            except IndexError as e:
                raise ValueError(f"Too many arguments provided, expected {len(self._args)} at most") from e

            value = arg.parse(word)
            if arg_index == 0:
                arguments[arg.name] = [value] if arg.nargs > 1 else value
            else:
                arguments[arg.name].append(value)

            if arg.nargs > (arg_index + 1):
                arg_index += 1
            else:
                arg_index = 0
                index += 1

            if arg.mutually_exclusive:
                mutually_exclusive_groups[arg.mutually_exclusive_group].add(arg.name)

        try:
            arguments.update(
                {
                    self._kwargs_aliases[name].name: self._kwargs_aliases[name].parse(value)
                    for name, value in kwargs.items()
                }
            )
            arguments.update({self._kwargs_aliases[name].name: True for name in args if name not in positional_args})
        except KeyError as e:
            raise ValueError(f"Unknown keyword argument: {e}") from e

        for name in arguments:
            if (arg := self._kwargs_aliases.get(name)) and arg.mutually_exclusive:
                mutually_exclusive_groups[arg.mutually_exclusive_group].add(name)

        for group_args in mutually_exclusive_groups.values():
            if len(group_args) > 1:
                raise ValueError(f"Only one of the arguments can be provided: {', '.join(group_args)}")

        return positional_args, arguments

    @property
    def kwargs(self) -> Any:
        return self._kwargs.items()

    @property
    def kwargs_aliases(self) -> Any:
        return self._kwargs_aliases.items()

    def get_argument(self, name: str) -> Arg | None:
        return self._all_args.get(name)

    def locate_argument(self, text: str) -> tuple[Arg | None, bool, int | None]:
        """Locate argument from text."""
        try:
            args = shlex.split(text)
        except ValueError:
            args = []

        if args and "=" in args[-1]:
            name = args[-1].split("=")[0]
            if name in self._kwargs_aliases:
                return self._kwargs_aliases[name], False, text.rindex("=") + 1

        posargs = [arg for arg in args if "=" not in arg]
        if text.endswith(" "):
            posargs += [""]
        if self._args and len(posargs) <= len(self._args):
            try:
                if posargs:
                    arg_index = text.rindex(posargs[-1])
                    if arg_index > 0 and text[arg_index - 1] in ('"', "'"):
                        arg_index -= 1
                else:
                    arg_index = 0
                return self._args[len(posargs) - 1 if posargs else 0], True, arg_index
            except ValueError:
                pass

        return None, False, None

    def verify(self, args: dict, nargs: int):
        """Verify argument requirements were met."""
        max_args = 0
        unmet_requirements = []
        for arg in self._args:
            max_args += arg.nargs
            if not arg.requirement_met(arg.name in args, nargs):
                unmet_requirements.append(arg.name)

        if unmet_requirements:
            raise ValueError(f"{', '.join(unmet_requirements)} expected")

        for arg in self._args:
            if arg.required and arg.name not in args:
                unmet_requirements.append(arg.name)

        if unmet_requirements:
            raise ValueError(f"{', '.join(unmet_requirements)} required")

        if nargs > max_args:
            raise ValueError(f"Expected at most {max_args} arguments, {nargs} provided")

    @cached_property
    def signature(self) -> str:
        signature = defaultdict(list)
        last_group = None
        current_requirement = None
        for index, arg in enumerate(self._args):
            arg_signature = arg.signature()
            if arg.required_at is not None and (index + 1) >= arg.required_at:
                if current_requirement != arg.required_at:
                    arg_signature = f"[{arg_signature}"
                current_requirement = arg.required_at

            last_group = arg.mutually_exclusive_group
            signature[arg.mutually_exclusive_group].append(arg_signature)

        if current_requirement is not None:
            signature[last_group][-1] = f"{signature[last_group][-1]}]"

        for arg in self._kwargs.values():
            signature[arg.mutually_exclusive_group].append(arg.signature(False))

        signature = " ".join(
            f"({' | '.join(args)})" if name and args else " ".join(args) for name, args in signature.items()
        )

        if self.display_name:
            return f"{self.name} {signature}".strip()
        return f"{signature}".strip()

    @property
    def has_example(self) -> bool:
        return bool(self._example)

    @property
    def example(self) -> str:
        if self.display_name:
            return f":{self.name} {self._example}".strip()
        return f":{self._example}".strip()


def parse_command_args(command: str) -> tuple[str | None, list[Any], dict[str, Any]]:
    """Parse command and args, returning name, posargs, kwargs."""
    command = command.strip()
    if not command:
        return None, [], {}

    words = shlex.split(command)
    command = words[0]
    args = words[1:]

    positional_args = [arg for arg in args if "=" not in arg]
    kwargs = {}
    for arg in args:
        if "=" not in arg:
            continue
        name, value = arg.split("=")
        if name in kwargs:
            if isinstance(kwargs[name], list):
                kwargs[name].append(value)
            else:
                kwargs[name] = [kwargs[name], value]
        else:
            kwargs[name] = value

    return command, positional_args, kwargs


class Mask(int):
    """Integer wrapper for mask arguments allowing for inverting mask."""

    def __new__(cls, value: int | str) -> Any:
        if isinstance(value, str):
            if value[0] == "~":
                value = ~int(value[1:], base=0)
            else:
                return super().__new__(cls, value, base=0)

        return super().__new__(cls, value)


completable_commands = [
    CommandDefinition(
        Commands.HELP,
        "Print Help",
        args=(Arg("command", types=Commands),),
    ),
    CommandDefinition(Commands.QUIT, "Quit/exit catscan"),
    CommandDefinition(
        Commands.CLEAR,
        "Clear search and/or highlighting (if unspecified, clears both)",
        example="highlights",
        kwargs=(
            Arg.Optional("search"),
            Arg.Optional("highlights", aliases=["highlight", "highlighting"]),
        ),
    ),
    CommandDefinition(
        Commands.SUMMARIZE,
        "Display histogram of events in row (optionally limited to the region between two marks). Supply one or more fields to summarize their values instead of the default (event abbreviation)",
        example="c a",
        args=(
            Arg("starting_mark", required_if=1),
            Arg("ending_mark", required_if=1),
        ),
        kwargs=(Arg("fields", aliases=["field"], multiple=True),),
    ),
    CommandDefinition(
        Commands.SEARCH,
        "Search events for the supplied search string and specifications. `row=` and `field=` filters apply to all searches, but `mask=` applies only to integral searches and `match_case=` applies only to textual searches",
        example="ldp row=*.iq_write match_case=yes",
        args=(Arg.Required("search_string"),),
        kwargs=(
            Arg("rows", aliases=["row"], choices=["current"], multiple=True),
            Arg("fields", aliases=["field"], multiple=True),
            Arg("mask", aliases=["bitmask"], types=Mask),
            Arg("match_case", aliases=["case_sensitive"], types=bool),
            Arg("type", aliases=["match_type"], types=MatchType, default=MatchType.AUTO),
            Arg("min_per_cycle", aliases=["per_cycle"], types=int),
        ),
    ),
    CommandDefinition(
        Commands.SEARCH_ROW,
        "Search events in the current row",
        example="ldp",
        args=(Arg.Required("search_string"),),
    ),
    CommandDefinition(
        Commands.SEARCH_FIELD,
        "Search only in a specified field",
        example="*.inum 789",
        args=(
            Arg.Required("field_glob"),
            Arg.Required("search_string"),
        ),
    ),
    CommandDefinition(
        Commands.SEARCH_INT,
        "Search integer events, with optional mask",
        example="0x4008ac 0xfffffc0",
        args=(
            Arg.Required("search_integer", types=int),
            Arg("integer_mask", types=Mask),
        ),
    ),
    CommandDefinition(
        Commands.SYNC_COMMITS,
        "Synchronize the time axis in the UI to the committed instruction 'inum' of another catscan process if a filename is provided (same command/filename must be executed in other process to sync with). 'stop' stops in-process syncing",
        example="/tmp/sync_commits.nodejs-cluster.14338_31",
        args=(Arg("fifo_basename", mutually_exclusive="action"),),
        kwargs=(Arg.Optional("stop", aliases=["no", "off"], mutually_exclusive="action"),),
    ),
    CommandDefinition(
        Commands.PIN_ROW,
        "Pin selected or named row(s)",
        args=(Arg("row_glob"),),
    ),
    CommandDefinition(
        Commands.UNPIN_ROW,
        "Unpin selected or named row(s) or all rows",
        args=(Arg("row_glob", mutually_exclusive="what"),),
        kwargs=(Arg.Optional("all", mutually_exclusive="what"),),
    ),
    CommandDefinition(
        Commands.ZOOM,
        "Zoom to level",
        args=(
            Arg.Required(
                "level_or_type",
                types=(Fraction, ZoomTypes),
            ),
            Arg("marks", types=str, nargs="+"),
        ),
        kwargs=(Arg.Optional("characters", aliases=["chars"]),),
    ),
    CommandDefinition(
        Commands.MARKS,
        "Show all marks",
    ),
]
default_commands = [
    CommandDefinition(
        DefaultCommands.GOTO_TIME,
        "Goto time (ps, ns, us, ms, etc.) or cycle (default)",
        example="2412ps",
        specification=re.compile(r"([0-9\.]+)(ps|ns|us|ms|s|cy?c?l?e?s?)?$"),
        display_name=False,
        args=(Arg("number with unit"),),
    ),
    CommandDefinition(
        DefaultCommands.GOTO_EVENT_ROW,
        "Go to the next row matching the event name glob",
        example="*.instruction_commit",
        display_name=False,
        args=(Arg("event_name_glob"),),
    ),
]
all_commands = completable_commands + default_commands
completable_command_definitions = {command.name: command for command in completable_commands}
command_definitions = {command.name: command for command in all_commands}


class CommandCompletion(FilteredSuggestions):
    """Command and argument completion."""

    def __init__(self, commands: dict[str, CommandDefinition] | CommandDefinition, **kwargs: Any):
        self._original_prefix = None
        self._cmd_only = not isinstance(commands, dict)
        self._cmd = None
        self._cmds = None
        if self._cmd_only:
            self._cmd = commands
        else:
            self._cmds = commands
        self._pos_arg = False
        self._arg = None

        super().__init__(**kwargs)

    def _assign(self, text: str | None):
        super()._assign(text)
        self._original_prefix = None

        args = [] if text is None else text.split(" ")
        if text is not None and (self._cmd_only or (len(args) > 1 and args[0] in self._cmds)):
            if self._cmd_only:
                arg_index = 0
                arg_text = text
            else:
                self._cmd = self._cmds[args[0]]
                arg_index = text.index(" ") + 1
                arg_text = text[arg_index:]

            self._arg, self._pos_arg, value_index = self._cmd.locate_argument(arg_text)
            value_index = value_index or ((arg_text.rindex(" ") + 1) if " " in arg_text else 0)
            self._original = text[arg_index + value_index :]
            self._original_prefix = text[: arg_index + value_index]
        else:
            self._arg = None
            if not self._cmd_only:
                self._cmd = None

    def _get(self) -> str | None:
        text = super()._get()
        if text is not None:
            return (self._original_prefix or "") + text
        return None

    def suggestions(self) -> list[str]:
        if self._cmd is None:
            return list(self._cmds.keys())

        options = [] if self._arg is None else self._arg.choices
        if self._arg is None or (self._pos_arg and not self._arg.required):
            options += [key for key, _arg in self._cmd.kwargs]

        return options

    def fallback_suggestions(self) -> list[str]:
        return (
            []
            if self._cmd is None or not (self._arg is None or (self._pos_arg and not self._arg.required))
            else [key for key, _arg in self._cmd.kwargs_aliases]
        )

    def reset(self, current_text: str | None = None) -> str:
        if current_text and current_text.endswith("=") and self._cmd is not None:
            arg_name = current_text.split(" ")[-1][:-1]
            if (arg := self._cmd.get_argument(arg_name)) and arg.valueless:
                current_text = current_text[:-1] + " "

        super().reset(current_text)
        return current_text
