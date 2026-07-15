# Copyright (c) 2019-2024 Ampere Computing. All rights reserved.
# SPDX-License-Identifier: BSD-3-Clause

import argparse
import logging
import re
import struct
from abc import ABC, abstractmethod
from collections.abc import Callable
from types import MethodType
from typing import Any

import capstone
from perf_streams.event_stream import Event


def value_string_abbreviation_spec(arg: str) -> "ValueStringAbbreviation":
    parts = arg.split(":", 2)
    if len(parts) < 2:
        raise argparse.ArgumentTypeError(
            "ValueStringAbbreviation must be <pattern>[,<pattern>...]:<value_suffix>[:<exclude_pattern>[,<exclude_pattern>...]]"
        )

    patterns_str, value_suffix, *exclude_parts = parts
    patterns = [pattern.strip() for pattern in patterns_str.split(",") if pattern.strip()]
    if not patterns:
        raise argparse.ArgumentTypeError("ValueStringAbbreviation patterns must be non-empty")
    if not value_suffix:
        raise argparse.ArgumentTypeError("ValueStringAbbreviation value suffix must be non-empty")

    exclude = None
    if exclude_parts:
        exclude_str = exclude_parts[0]
        exclude = [pattern.strip() for pattern in exclude_str.split(",") if pattern.strip()] or None

    return ValueStringAbbreviation(patterns, value_suffix=value_suffix, exclude=exclude)


def value_map_abbreviation_spec(arg: str) -> "ValueMapAbbreviation":
    parts = arg.split(":")
    if len(parts) < 3:
        raise argparse.ArgumentTypeError(
            "ValueMapAbbreviation must be <pattern>[,<pattern>...]:<value_suffix>:<key>=<value>[,<key>=<value>...]"
            "[:default=<default>][:exclude=<exclude_pattern>[,<exclude_pattern>...]][:default_on_missing]"
        )

    patterns_str, value_suffix, map_str, *extra_parts = parts
    patterns = [pattern.strip() for pattern in patterns_str.split(",") if pattern.strip()]
    if not patterns:
        raise argparse.ArgumentTypeError("ValueMapAbbreviation patterns must be non-empty")
    if not value_suffix:
        raise argparse.ArgumentTypeError("ValueMapAbbreviation value suffix must be non-empty")

    map_entries = [entry.strip() for entry in map_str.split(",") if entry.strip()]
    if not map_entries:
        raise argparse.ArgumentTypeError("ValueMapAbbreviation map entries must be non-empty")

    value_map: dict[int, str] = {}
    for entry in map_entries:
        if "=" not in entry:
            raise argparse.ArgumentTypeError("ValueMapAbbreviation map entries must be <key>=<value>")
        key_str, mapped_value = entry.split("=", 1)
        key_str = key_str.strip()
        mapped_value = mapped_value.strip()
        if not key_str or not mapped_value:
            raise argparse.ArgumentTypeError("ValueMapAbbreviation map entries must be <key>=<value>")
        try:
            key = int(key_str, 0)
        except ValueError as exc:
            raise argparse.ArgumentTypeError(f"ValueMapAbbreviation map key '{key_str}' must be an integer") from exc
        value_map[key] = mapped_value

    default = None
    exclude = None
    default_on_missing = False
    for extra in extra_parts:
        if extra.startswith("default="):
            default_value = extra.split("=", 1)[1]
            if not default_value:
                raise argparse.ArgumentTypeError("ValueMapAbbreviation default value must be non-empty")
            default = default_value
        elif extra.startswith("exclude="):
            exclude_str = extra.split("=", 1)[1]
            exclude = [pattern.strip() for pattern in exclude_str.split(",") if pattern.strip()] or None
            if exclude is None:
                raise argparse.ArgumentTypeError("ValueMapAbbreviation exclude patterns must be non-empty")
        elif extra in {"default_on_missing", "default-on-missing"}:
            default_on_missing = True
        else:
            raise argparse.ArgumentTypeError(
                "ValueMapAbbreviation extra options must be default=<value>, "
                "exclude=<pattern>[,<pattern>...], or default_on_missing"
            )

    return ValueMapAbbreviation(
        patterns,
        value_suffix=value_suffix,
        value_map=value_map,
        default=default,
        exclude=exclude,
        default_on_missing_value_suffix=default_on_missing,
    )


class DynamicAbbreviation(ABC):
    def __init__(self, patterns: list[str], exclude_patterns: list[str] | None):
        self.patterns = [re.compile(pattern) for pattern in patterns]
        self.exclude_patterns = [re.compile(pattern) for pattern in exclude_patterns] if exclude_patterns else []

    def setup(self, mapper: "Mapper") -> None:  # noqa: B027
        """Sets up any info from mapper for generation (e.g. disassembler)."""

    def value_with_suffix(self, event: Event, suffix: str) -> str | None:
        for name, value in event.data.items():
            if name.endswith(suffix):
                return value

        return None

    def might_generate(self, name: str) -> bool:
        """
        Given the name of an event, determine if this class might be
        able to generate an abbreviation for the given event.
        """
        return any(pattern.search(name) for pattern in self.patterns) and not any(
            pattern.search(name) for pattern in self.exclude_patterns
        )

    @abstractmethod
    def generate(self, event: Event) -> str | None:
        """
        Attempt to generate the abbreviation for the supplied event.
        Returns a non-empty string if it supplied the abbreviation and
        None if it did not.
        """


class ValueStringAbbreviation(DynamicAbbreviation):
    def __init__(
        self,
        patterns: list[str],
        value_suffix: str,
        exclude: list[str] | None = None,
        transform: Callable[[str], str] | None = None,
    ):
        self.value_suffix = value_suffix
        self.transform = transform
        super().__init__(patterns, exclude)

    def generate(self, event: Event) -> str | None:
        value = self.value_with_suffix(event, self.value_suffix)
        if value is None:
            return None
        value = str(value)
        if self.transform is not None:
            value = self.transform(value)
        return value


class ValueListAbbreviation(DynamicAbbreviation):
    def __init__(self, patterns: list[str], value_suffix: str, value_list: list[str], exclude: list[str] | None = None):
        self.value_suffix = value_suffix
        self.value_list = value_list
        super().__init__(patterns, exclude)

    def generate(self, event: Event) -> str | None:
        value = self.value_with_suffix(event, self.value_suffix)
        if value is None:
            return None
        try:
            index = int(value)
            return self.value_list[index]
        except IndexError:
            logging.warning(f"Index of {index} for event named {event.name} out-of-bounds for rename")
            return value
        except ValueError:
            return value


class ValueMapAbbreviation(DynamicAbbreviation):
    def __init__(
        self,
        patterns: list[str],
        value_suffix: str,
        value_map: dict[int, str],
        default: str | None = None,
        exclude: list[str] | None = None,
        default_on_missing_value_suffix: bool = False,
    ):
        self.value_suffix = value_suffix
        self.value_map = value_map
        self.default = default
        self.default_on_missing_value_suffix = default_on_missing_value_suffix
        super().__init__(patterns, exclude)

    def generate(self, event: Event) -> str | None:
        value = self.value_with_suffix(event, self.value_suffix)
        if value is None:
            return self.default if self.default_on_missing_value_suffix else None
        try:
            value = int(value)
            return self.value_map[value]
        except KeyError:
            if self.default is None:
                logging.warning(f"Value of {value} for event named {event.name} out-of-bounds for value_map")
            return self.default
        except ValueError:
            return value


class CallableAbbreviation(DynamicAbbreviation):
    def __init__(
        self,
        patterns: list[str],
        generate: Callable[[object, Event], str | None],
        setup: Callable[[object, "Mapper"], None] | None = None,
        exclude: list[str] | None = None,
    ):
        if setup is not None:
            self.setup = MethodType(setup, self)
        self.generate = MethodType(generate, self)
        super().__init__(patterns, exclude)

    def generate(self, event: Event) -> str | None:
        raise Exception("Should be overridden")


disassembly_architectures = {
    "armv7": (capstone.CS_ARCH_ARM, capstone.CS_MODE_ARM),
    "arm64": (capstone.CS_ARCH_ARM64, capstone.CS_MODE_ARM),
    "x86_64": (capstone.CS_ARCH_X86, capstone.CS_MODE_64),
}


class Mapper:
    def __init__(
        self,
        event_groups: list[str] = None,
        hex_args: list[str] | None = None,
        inst_args: list[str] | None = None,
        static_abbreviations: dict | None = None,
        dynamic_abbreviations: list | None = None,
        instruction_arch: str = "arm64",
    ):
        if event_groups:
            options = "|".join([f"({g})" for g in event_groups])
            self.event_groups = re.compile(f"(?P<prefix>{options})\\.(?P<tail>.*)")
        else:
            self.event_groups = None
        self.regex_hexargs = self._fields_to_regex(hex_args)
        self.regex_instargs = self._fields_to_regex(inst_args)
        self.abbreviation_lookups: dict[str, list[DynamicAbbreviation]] = {}
        self.values_no_modify = set()
        self.static_abbreviations = static_abbreviations or {}
        self.dynamic_abbreviations = dynamic_abbreviations or []

        disasm_arch, disasm_mode = disassembly_architectures[instruction_arch]
        self.disassembler = capstone.Cs(disasm_arch, disasm_mode)

        for abbreviation in self.dynamic_abbreviations:
            abbreviation.setup(self)

    def process(self, name: str, *, default_group: str | None = None) -> tuple[str, str]:
        default_group = default_group or "Events"
        if self.event_groups:
            m = re.match(self.event_groups, name)
            if m:
                return (m.group("prefix"), m.group("tail"))
        return default_group, name

    def event_name_to_abbrev(self, event: Event) -> str:
        name = event.name
        if name not in self.abbreviation_lookups:
            self.abbreviation_lookups[name] = [
                abbreviation for abbreviation in self.dynamic_abbreviations if abbreviation.might_generate(name)
            ]

        for abbreviation in self.abbreviation_lookups[name]:
            abbrev = abbreviation.generate(event)
            if abbrev is not None:
                return abbrev

        if name not in self.static_abbreviations:
            self.static_abbreviations[name] = name.split(".")[-1][0]
        return self.static_abbreviations[name]

    def value_with_suffix(self, event: Event, suffix: str) -> str | None:
        for name, value in event.data.items():
            if name.endswith(suffix):
                return value

        return None

    def rename_from_list(self, index: Any, values: list[str], default: str | None = None) -> str:
        if not isinstance(index, int):
            return index

        try:
            return values[index]
        except IndexError:
            logging.warning(f"Value of {index} out-of-bounds for rename")
            return str(index) if default is None else default

    def rename_suffix_from_list(self, event: Event, suffix: str, values: list[str], default: str | None = None) -> str:
        try:
            index = int(self.value_with_suffix(event, suffix))
            return values[index]
        except IndexError:
            logging.warning(f"Value of {index} for {suffix} out-of-bounds for rename")
            return str(index) if default is None else default
        except ValueError:
            return index

    def disasm_single_instruction(self, value: Any) -> str:
        try:
            instructions = list(self.disassembler.disasm_lite(struct.pack("<I", value), 0x1000))
            assert len(instructions) == 1
            (_, _, mnemonic, op_str) = instructions[0]

            return mnemonic + " " + op_str
        except:
            return "Illegal instruction"

    def modify_args(self, args: dict):
        changes = {}

        for name, value in args.items():
            if name in self.values_no_modify:
                continue

            if self.regex_instargs.match(name):
                changes[name] = hex(int(value))
                changes["disassembly"] = self.disasm_single_instruction(value)
            elif self.regex_hexargs.match(name):
                changes[name] = hex(int(value, base=0) if isinstance(value, str) else value)
            else:
                self.values_no_modify.add(name)

        args.update(changes)

    def _fields_to_regex(self, fields: list[str]) -> re.Pattern:
        re_str = "^((" + ")|(".join(fields) + "))$"
        return re.compile(re_str)
