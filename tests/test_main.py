# Copyright (c) 2026 Ampere Computing. All rights reserved.
# SPDX-License-Identifier: BSD-3-Clause

import logging
import os
import sys
import unittest
from contextlib import suppress
from functools import partial
from io import BufferedReader, StringIO, TextIOWrapper
from pathlib import Path
from tempfile import NamedTemporaryFile, TemporaryDirectory, TemporaryFile

import urwid
from perf_streams.event_stream import EventStreamWriter
from test_data import CatscanDataTest

from catscan.__main__ import load_mapping_file_abbreviations, setup
from catscan.events import trace_events
from catscan.events.mapping import ValueStringAbbreviation

TOTAL_EVENTS = 20
PS_PER_CYCLE = 100


class Args:
    def __init__(self, **kwargs):
        self.view = "unspecified"
        self.cache = None
        self.instruction_arch = "arm64"
        self.period = PS_PER_CYCLE
        self.sort_keys = True
        self.instruction_commit_event = "core.commit"
        self.instruction_commit_index = "core.inum"
        self.convert_enumerations = True
        self.debug = True

        self.__dict__.update(kwargs)

    def __getattr__(self, name):
        return []


class TestingScreen(urwid.display.raw.Screen):
    def __init__(self, output):
        self.input_r_fd, self.input_w_fd = os.pipe()

        self.input_r_raw = os.fdopen(self.input_r_fd, "rb")
        self.input_r_buf = BufferedReader(self.input_r_raw)
        self.input_r = TextIOWrapper(self.input_r_buf, encoding="utf-8")

        self.input_w_buf = os.fdopen(self.input_w_fd, "wb")
        self.input_w = TextIOWrapper(self.input_w_buf, encoding="utf-8")

        self.output = output
        super().__init__(input=self.input_r, output=self.output)

    def get_cols_rows(self):
        return (512, 512)

    def read_all(self):
        self.output.seek(0)
        return self.output.read()

    def do(self, command_or_motion):
        self.input_w.write(command_or_motion)
        if command_or_motion.startswith(":"):
            self.input_w.write("\r\n")
        self.input_w.flush()

    def close(self):
        for file in (self.input_r, self.input_w):
            with suppress(ValueError):
                file.close()

    def __del__(self):
        self.close()


class TestMappingFileAbbreviations(unittest.TestCase):
    def write_mapping_file(self, directory: str, filename: str, text: str) -> str:
        path = Path(directory) / filename
        path.write_text(text, encoding="utf-8")
        return str(path)

    def test_loads_dynamic_abbreviation_from_mapping_file(self):
        with TemporaryDirectory() as directory:
            mapping_file = self.write_mapping_file(
                directory,
                "mapping.py",
                """
from catscan.events.mapping import ValueStringAbbreviation

add_abbreviation(ValueStringAbbreviation(["event_.*"], value_suffix="status"))
""",
            )

            abbreviations = load_mapping_file_abbreviations([mapping_file])

        self.assertEqual(1, len(abbreviations))
        self.assertIsInstance(abbreviations[0], ValueStringAbbreviation)
        self.assertEqual("status", abbreviations[0].value_suffix)

    def test_rejects_non_dynamic_abbreviation_from_mapping_file(self):
        with TemporaryDirectory() as directory:
            mapping_file = self.write_mapping_file(
                directory,
                "mapping.py",
                """
add_abbreviation("invalid")
""",
            )

            with self.assertRaisesRegex(TypeError, "expected DynamicAbbreviation, got str"):
                load_mapping_file_abbreviations([mapping_file])

    def test_loads_multiple_mapping_files_in_order(self):
        with TemporaryDirectory() as directory:
            first_mapping_file = self.write_mapping_file(
                directory,
                "first_mapping.py",
                """
from catscan.events.mapping import ValueStringAbbreviation

add_abbreviation(ValueStringAbbreviation(["first"], value_suffix="first_suffix"))
""",
            )
            second_mapping_file = self.write_mapping_file(
                directory,
                "second_mapping.py",
                """
from catscan.events.mapping import ValueStringAbbreviation

add_abbreviation(ValueStringAbbreviation(["second"], value_suffix="second_suffix"))
""",
            )

            abbreviations = load_mapping_file_abbreviations([first_mapping_file, second_mapping_file])

        self.assertEqual(
            ["first_suffix", "second_suffix"], [abbreviation.value_suffix for abbreviation in abbreviations]
        )


class TestMain(CatscanDataTest):
    @classmethod
    def event_stream_setup(cls):
        writer = EventStreamWriter(cls.test_filename)
        events = [writer.define_event(f"event_{number}", "some event") for number in range(TOTAL_EVENTS)]
        writer.start_simulation()
        for cycle in range(1000):
            time = cycle * PS_PER_CYCLE
            for index, event in enumerate(events):
                if cycle % (index + 1) == 0:
                    writer.post_event(event, time=time)

        writer.close()

        events = [trace_events.trace_spec("event_*")]
        cls.set_event_stream_params(events=events)

    def args(self, **kwargs):
        return Args(
            input=self.test_filename, log=self.logging.name, event=[trace_events.trace_spec("event_*")], **kwargs
        )

    def do(self, command_or_motion):
        self.screen.do(command_or_motion)

    def press_key(self, top, key):
        return top.keypress(self.screen.get_cols_rows(), key)

    def run_catscan(self, args, *steps: tuple[int, str]):
        top = setup(args, screen=self.screen)

        for delay, command_or_motion in steps:
            top.main_loop.event_loop.alarm(delay, partial(self.do, command_or_motion))

        delay = steps[-1][0] if steps else 0
        top.main_loop.event_loop.alarm(delay + 1, partial(self.do, ":quit"))

        top.main_loop.run()
        return self.screen.read_all()

    def setUp(self):
        self.logging = NamedTemporaryFile()
        self.output = TemporaryFile("w+")
        self.screen = TestingScreen(self.output)
        self.capture_asyncio_logs()

    def capture_asyncio_logs(self):
        self.asyncio_log_stream = StringIO()
        self.asyncio_log_handler = logging.StreamHandler(self.asyncio_log_stream)
        self.asyncio_log_handler.setFormatter(logging.Formatter("%(levelname)s:%(name)s:%(message)s"))

        self.asyncio_logger = logging.getLogger("asyncio")
        self.asyncio_handlers = self.asyncio_logger.handlers[:]
        self.asyncio_level = self.asyncio_logger.level
        self.asyncio_propagate = self.asyncio_logger.propagate

        self.asyncio_logger.handlers = [self.asyncio_log_handler]
        self.asyncio_logger.setLevel(logging.DEBUG)
        self.asyncio_logger.propagate = False

    def _test_failed(self):
        current_test = self.id()
        result = self._outcome.result
        return any(test.id() == current_test for test, _ in result.errors + result.failures)

    def tearDown(self):
        self.asyncio_logger.handlers = self.asyncio_handlers
        self.asyncio_logger.setLevel(self.asyncio_level)
        self.asyncio_logger.propagate = self.asyncio_propagate

        captured_logs = self.asyncio_log_stream.getvalue()
        if captured_logs and self._test_failed():
            sys.stderr.write(f"\nCaptured asyncio logs for {self.id()}:\n{captured_logs}")

        self.asyncio_log_handler.close()
        self.asyncio_log_stream.close()
        self.screen.close()
        self.output.close()
        self.logging.close()

    def test_open(self):
        out = self.run_catscan(self.args())
        self.assertIn("Loading (100%)", out)
        for event in range(TOTAL_EVENTS):
            self.assertIn(f"event_{event}", out)

    def test_help(self):
        out = self.run_catscan(self.args(), (1, "?"), (2, "q"))
        self.assertIn("Help / Input Mappings", out)

    def test_quit_keybinding(self):
        top = setup(self.args(), screen=self.screen)

        self.assertEqual(self.press_key(top, "Z"), "Z")
        with self.assertRaises(urwid.ExitMainLoop):
            self.press_key(top, "Z")
