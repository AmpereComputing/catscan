#!/usr/bin/env python3

# Copyright (c) 2024 Ampere Computing. All rights reserved.
# SPDX-License-Identifier: BSD-3-Clause

import argparse
import asyncio
import logging
import os
import runpy
import warnings

import urwid

from catscan import events
from catscan.argument_parser import ArgumentParser
from catscan.colors import palette
from catscan.events import trace_events
from catscan.events.mapping import (
    DynamicAbbreviation,
    Mapper,
    disassembly_architectures,
    value_map_abbreviation_spec,
    value_string_abbreviation_spec,
)
from catscan.widgets.top import Top

HEXARGS_DEFAULT = [
    "program_counter",
    "virtual_address",
    "physical_address",
    "cacheline",
]
# Default instruction fields
INSTARGS_DEFAULT = [
    "opcode",
    "instruction",
    "inst_bytes",
]


def static_abbreviation_spec(arg: str) -> tuple[str, str]:
    if "=" not in arg:
        raise argparse.ArgumentTypeError("Static abbreviation must be <event>=<abbrev>")
    event, abbrev = [part.strip() for part in arg.split("=", 1)]
    if not event or not abbrev:
        raise argparse.ArgumentTypeError("Static abbreviation must be <event>=<abbrev>")
    return event, abbrev


def load_mapping_file_abbreviations(mapping_files: list[str]) -> list[DynamicAbbreviation]:
    abbreviations: list[DynamicAbbreviation] = []

    for mapping_file in mapping_files:

        def add_abbreviation(abbreviation: object, *, mapping_file: str = mapping_file) -> None:
            if not isinstance(abbreviation, DynamicAbbreviation):
                raise TypeError(
                    f"{mapping_file}: add_abbreviation() expected DynamicAbbreviation, "
                    f"got {type(abbreviation).__name__}"
                )
            abbreviations.append(abbreviation)

        runpy.run_path(mapping_file, init_globals={"add_abbreviation": add_abbreviation})

    return abbreviations


def parse_args() -> argparse.Namespace:
    parser = ArgumentParser(description=__doc__, tool="catscan", comments=True)
    parser.add_argument("input", nargs="?", default="events.es", help="event stream file")
    parser.add_argument("--log", default=None, help="Log to specified filename")
    parser.add_argument(
        "--cache",
        nargs="?",
        default=None,
        const="",
        help='Cache a "pickled" version of this event stream, once loaded (supply path to override default)',
    )
    parser.add_argument("--view", default="unspecified", help="type of view to generate")
    parser.add_argument(
        "--onload-command",
        default=[],
        action="append",
        help="commands to execute as soon as the event stream is loaded",
    )
    parser.add_argument(
        "--onsync-command",
        default=[],
        action="append",
        help="commands to execute when a new sync session is fully initialized",
    )
    parser.add_argument(
        "-e",
        "--event",
        metavar="GLOB",
        default=[],
        action="append",
        type=trace_events.trace_spec,
        help="event to include in output",
    )
    parser.add_argument(
        "--occupancy", metavar="NAME:START,END(,DATA)", default=[], action="append", help="occupancy of a buffer"
    )
    parser.add_argument(
        "--hex",
        metavar="REGEX",
        default=HEXARGS_DEFAULT,
        action="append",
        help="data field to display in hexadecimal format",
    )
    parser.add_argument(
        "--instruction",
        metavar="REGEX",
        default=INSTARGS_DEFAULT,
        action="append",
        help="data field to treat as an instruction and run disassembly on, also applies hex format",
    )
    parser.add_argument(
        "--instruction-arch",
        metavar="ARCH",
        default="arm64",
        choices=sorted(disassembly_architectures),
        help="instruction disassembly architecture (default: arm64)",
    )
    parser.add_argument("--period", help="clock period in picoseconds", type=int, default=None)
    parser.add_argument(
        "--no-sort-keys",
        dest="sort_keys",
        action="store_false",
        default=True,
        help="Disable sorting of (data value list) keys in event view",
    )
    parser.add_argument(
        "--post-to-tx",
        action="append",
        default=[],
        help='Post data from an event to the containing transaction (syntax: "<event>:<data>[=<alias>]"). Data which is not pulled from will be displayed under an events transcation data in the sidebar.',
    )
    parser.add_argument(
        "--pull-from-tx",
        action="append",
        default=[],
        help='Pull data from the transaction to an event (syntax: "<event>:<data/alias>")',
    )

    parser.add_argument("--tx-name", action="append", help="transaction name data field")
    parser.add_argument(
        "--tx-start", action="append", help="transaction start event", type=trace_events.trace_spec, default=None
    )
    parser.add_argument(
        "--tx-end", action="append", help="transaction end event", type=trace_events.trace_spec, default=None
    )

    parser.add_argument(
        "--split",
        action="append",
        type=trace_events.split_spec,
        default=[],
        help='Split an event into multiple events based on data values (syntax: "<event>/<data>[/<data>...]=<format>", where \\{<data>\\} with the data name will insert the value for that split into the name.',
    )
    parser.add_argument(
        "--split-all",
        action="append",
        type=trace_events.split_spec,
        default=[],
        help='Split and include an event into multiple events based on data values (syntax: "<event>/<data>[/<data>...]=<format>", where \\{<data>\\} with the data name will insert the value for that split into the name.',
    )
    parser.add_argument(
        "-r",
        "--rename",
        action="append",
        type=events.rename_spec,
        default=[],
        help='Rename event using regular expression-like syntax: <event>=<newname> (example: "pmu_split_(#)=pmu.something.\1")',
    )
    parser.add_argument(
        "-x",
        "--exclude",
        action="append",
        type=trace_events.trace_spec,
        default=[],
        help="Exclude event",
    )
    parser.add_argument(
        "--static-abbrev",
        action="append",
        dest="static_abbreviations",
        type=static_abbreviation_spec,
        default=[],
        metavar="EVENT=ABBREV",
        help="Add a static abbreviation mapping (syntax: <event>=<abbrev>)",
    )
    parser.add_argument(
        "--value-string-abbrev",
        action="append",
        type=value_string_abbreviation_spec,
        default=[],
        metavar="PATTERNS:SUFFIX[:EXCLUDE_PATTERNS]",
        help="Add a ValueStringAbbreviation (syntax: <pattern>[,<pattern>...]:<value_suffix>[:<exclude_pattern>[,<exclude_pattern>...]])",
    )
    parser.add_argument(
        "--value-map-abbrev",
        action="append",
        type=value_map_abbreviation_spec,
        default=[],
        metavar="PATTERNS:SUFFIX:MAP[:default=DEFAULT][:exclude=PATTERNS][:default_on_missing]",
        help=(
            "Add a ValueMapAbbreviation (syntax: <pattern>[,<pattern>...]:<value_suffix>:"
            "<key>=<value>[,<key>=<value>...][:default=<default>][:exclude=<exclude_pattern>[,<exclude_pattern>...]]"
            "[:default_on_missing])"
        ),
    )
    parser.add_argument(
        "--mapping-file",
        action="append",
        default=[],
        metavar="PATH",
        help="Load a Python mapping file which can call add_abbreviation(DynamicAbbreviation)",
    )
    parser.add_argument(
        "--instruction-commit-event",
        help="The name of the event corresponding to instruction commit",
    )
    parser.add_argument(
        "--instruction-commit-index",
        help='The name of the event data item representing the committed instruction index (a.k.a. "inum")',
    )

    parser.add_argument(
        "-g", "--event-group", action="append", default=[], help="Generate a new group for this event prefix"
    )
    parser.add_argument(
        "--no-enums",
        dest="convert_enumerations",
        default=True,
        action="store_false",
        help="Don't convert enumerations from ES (i.e. just use mapper)",
    )
    parser.add_argument("--debug", action="store_true", help="Debugging logging info")

    args = parser.parse_args()
    args.static_abbreviations = dict(args.static_abbreviations)

    if args.log:
        logging.basicConfig(
            format="[%(relativeCreated)d] %(levelname)s:%(name)s %(message)s",
            filename=args.log,
            encoding="utf-8",
            level=logging.DEBUG if args.debug else logging.INFO,
        )

    return args


def setup(args: argparse.Namespace, screen: urwid.BaseScreen | None = None) -> Top:
    top = Top(args)
    event_loop = asyncio.new_event_loop()
    event_loop.set_debug(args.debug)
    asyncio.set_event_loop(event_loop)
    loop = urwid.MainLoop(
        urwid.AttrMap(top, "body"),
        palette=palette,
        event_loop=urwid.AsyncioEventLoop(loop=event_loop),
        screen=screen,
    )
    top.main_loop = loop  # Allow 'top' to reference the main loop (yuck!)

    # Try to use 256 colors if we think the terminal supports it
    colors = 16
    if "TERM" in os.environ and "256" in os.environ["TERM"]:
        colors = 256
    loop.screen.set_terminal_properties(colors)
    loop.screen.reset_default_terminal_palette()
    loop.screen.focus_reporting = True

    dynamic_abbreviations = (
        args.value_string_abbrev + args.value_map_abbrev + load_mapping_file_abbreviations(args.mapping_file)
    )
    mapper = Mapper(
        event_groups=args.event_group,
        hex_args=[],
        inst_args=args.instruction,
        static_abbreviations=args.static_abbreviations,
        dynamic_abbreviations=dynamic_abbreviations,
        instruction_arch=args.instruction_arch,
    )
    event_filters = []
    if args.split:
        event_filters.append(trace_events.EventSplitter(args.split))
    if args.split_all:
        event_filters.append(trace_events.EventSplitter(args.split_all, include=True))
    if args.rename:
        event_filters.append(trace_events.EventRenamer(args.rename))
    if args.event and args.split:
        event_filters.append(trace_events.EventIncluder(args.event))
    if args.exclude:
        event_filters.append(trace_events.EventExcluder(args.exclude))

    top.load_file(
        args.input,
        args.view,
        mapper,
        trace_events.EventFilters(event_filters, default_inclusion=not (args.event and args.split)),
        args.event,
        args.cache,
        args.post_to_tx,
        args.pull_from_tx,
        occupancy=args.occupancy,
        transaction_name=args.tx_name,
        transaction_start=args.tx_start,
        transaction_end=args.tx_end,
        convert_enumerations=args.convert_enumerations,
    )

    return top


def run(args: argparse.Namespace) -> None:
    top = setup(args)
    top.main_loop.run()


def main() -> None:
    args = parse_args()
    run(args)


if __name__ == "__main__":
    main()
