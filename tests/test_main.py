# Copyright (c) 2026 Ampere Computing. All rights reserved.
# SPDX-License-Identifier: BSD-3-Clause

import logging
import os
import sys
import unittest
from contextlib import suppress
from collections.abc import Iterable
from functools import partial
from io import BufferedReader, StringIO, TextIOWrapper
from pathlib import Path
from tempfile import NamedTemporaryFile, TemporaryDirectory, TemporaryFile

import urwid
from perf_streams.event_stream import EventStreamWriter
from test_data import CatscanDataTest

from catscan.__main__ import load_mapping_file_abbreviations, setup
from catscan.colors import palette
from catscan.events import trace_events
from catscan.events.mapping import ValueStringAbbreviation
from catscan.mouse_tracking import XTERM_ENABLE_ALL_MOTION
from catscan.state import HoverTarget
from catscan.user_input import ACTIONS, action_mouseevents, is_mouse_hover_event, translate_mouseevent
from catscan.widgets.event_sidebar import EventDetailDataText
from catscan.widgets.hover_popup import HoverPopup

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

    def test_setup_failure_does_not_enable_hover_tracking(self):
        with TemporaryDirectory() as directory, TemporaryFile("w+") as output:
            mapping_file = self.write_mapping_file(
                directory,
                "mapping.py",
                """
add_abbreviation("invalid")
""",
            )
            screen = TestingScreen(output)

            with self.assertRaisesRegex(TypeError, "expected DynamicAbbreviation, got str"):
                setup(Args(mapping_file=[mapping_file]), screen=screen)

            self.assertNotIn(XTERM_ENABLE_ALL_MOTION, screen.read_all())


class TestHoverTarget(unittest.TestCase):
    def test_accepts_zero_event_row_key(self):
        hover = HoverTarget(0, time_range=(0, PS_PER_CYCLE), within_transaction=True)

        self.assertTrue(hover)
        self.assertEqual(0, hover.event_row)


class TestMain(CatscanDataTest):
    @classmethod
    def event_stream_setup(cls):
        writer = EventStreamWriter(cls.test_filename)
        events = [writer.define_event(f"event_{number}", "some event") for number in range(TOTAL_EVENTS)]
        event_value = writer.define_data("event.value", "Value associated with the event")
        writer.start_simulation()
        for cycle in range(1000):
            time = cycle * PS_PER_CYCLE
            for index, event in enumerate(events):
                if cycle % (index + 1) == 0:
                    writer.post_event(event, time=time, values={event_value: cycle + index})

        writer.close()

        events = [trace_events.trace_spec("event_*")]
        cls.set_event_stream_params(events=events)
        cls.loaded_event_data = None

    def args(self, **kwargs):
        return Args(
            input=self.test_filename, log=self.logging.name, event=[trace_events.trace_spec("event_*")], **kwargs
        )

    def do(self, command_or_motion):
        self.screen.do(command_or_motion)

    def press_key(self, top, key):
        return top.keypress(self.screen.get_cols_rows(), key)

    def move_mouse_to_cell(self, top, cell: tuple[int, int]):
        col, row = cell
        frame_mouse_event = top.frame.mouse_event
        top.frame.mouse_event = lambda size, event, button, col, row, focus: True
        try:
            top.mouse_event((120, 40), "mouse drag", 4, col, row, True)
        finally:
            top.frame.mouse_event = frame_mouse_event

    def event_data(self):
        if self.__class__.loaded_event_data is None:
            self.__class__.loaded_event_data = self.load_event_data()
        return self.__class__.loaded_event_data

    def first_event(self, row: str):
        return next(self.event_data().event_rows[row][0:PS_PER_CYCLE])

    def canvas_text(self, lines: Iterable[bytes]) -> str:
        return "\n".join(line.decode() for line in lines)

    def loaded_top(self):
        top = setup(self.args(), screen=self.screen)
        top.cached_maxcol = 120
        top.update_state(top.state.copy_with(loading=False))
        top.update_stream_data(self.event_data())
        return top

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

    def test_enables_hover_tracking_after_screen_start(self):
        top = setup(self.args(), screen=self.screen)
        before_start = self.screen.read_all()

        try:
            top.main_loop.screen.start()
            after_start = self.screen.read_all()
        finally:
            top.main_loop.screen.stop()

        self.assertGreater(after_start.count(XTERM_ENABLE_ALL_MOTION), before_start.count(XTERM_ENABLE_ALL_MOTION))

    def test_help(self):
        out = self.run_catscan(self.args(), (1, "?"), (2, "q"))
        self.assertIn("Help / Input Mappings", out)

    def test_quit_keybinding(self):
        top = setup(self.args(), screen=self.screen)

        self.assertEqual(self.press_key(top, "Z"), "Z")
        with self.assertRaises(urwid.ExitMainLoop):
            self.press_key(top, "Z")

    def test_hover_coalescing(self):
        top = self.loaded_top()
        invalidations = []
        top._invalidate = lambda: invalidations.append(True)

        hover = HoverTarget("event_0", event=self.first_event("event_0"), view="main.resource")
        self.move_mouse_to_cell(top, (20, 4))

        self.assertTrue(top.on_hover(hover))
        self.assertFalse(top.on_hover(hover))
        self.assertEqual(1, len(invalidations))

        self.move_mouse_to_cell(top, (21, 4))
        self.assertTrue(top.on_hover(hover))
        self.assertEqual(2, len(invalidations))

        range_hover = HoverTarget("event_0", time_range=(0, 2 * PS_PER_CYCLE), view="main.resource")
        self.assertTrue(top.on_hover(range_hover))
        self.assertEqual(3, len(invalidations))

        self.assertTrue(top.clear_hover())
        self.assertFalse(top.clear_hover())
        self.assertEqual(4, len(invalidations))

    def test_buttonless_mouse_release_is_not_hover(self):
        top = self.loaded_top()
        dispatched = []

        def mouse_event(size, event, button, col, row, focus):
            dispatched.append((event, button))
            return True

        top.frame.mouse_event = mouse_event

        top.mouse_event((120, 40), "mouse press", 1, 0, 0, True)
        top.mouse_event((120, 40), "mouse release", 0, 0, 0, True)

        self.assertEqual(("mouse release", 1), dispatched[-1])

    def test_hover_mouse_action_translates_motion_forms(self):
        self.assertEqual(("mouse release", 1), translate_mouseevent("left_click"))
        self.assertEqual(list(translate_mouseevent("hover")), action_mouseevents[ACTIONS.HOVER])
        self.assertIn(("mouse drag", 0), action_mouseevents[ACTIONS.HOVER])
        self.assertIn(("mouse press", 0), action_mouseevents[ACTIONS.HOVER])
        self.assertIn(("mouse drag", 4), action_mouseevents[ACTIONS.HOVER])
        self.assertIn(("shift mouse drag", 4), action_mouseevents[ACTIONS.HOVER])

        self.assertTrue(is_mouse_hover_event("mouse drag", 0))
        self.assertTrue(is_mouse_hover_event("shift mouse drag", 4))
        self.assertFalse(is_mouse_hover_event("mouse release", 0))

    def test_hover_event_outside_event_view_clears_popup(self):
        top = self.loaded_top()
        self.move_mouse_to_cell(top, (20, 4))
        event = self.first_event("event_0")
        top.on_hover(HoverTarget("event_0", event=event, view="main.resource"))
        top.frame.mouse_event = lambda size, event, button, col, row, focus: False

        top.mouse_event((120, 40), "mouse drag", 4, 0, 0, True)

        self.assertFalse(top._hover_target)

    def test_hover_command_disables_single_event_hover(self):
        top = self.loaded_top()
        self.move_mouse_to_cell(top, (20, 4))
        event = self.first_event("event_0")

        top.on_hover(HoverTarget("event_0", event=event, view="main.resource"))
        self.assertTrue(top._hover_target)

        self.assertTrue(top.command("hover single=no"))
        self.assertFalse(top.single_event_hover_enabled)
        self.assertTrue(top.multiple_event_hover_enabled)
        self.assertFalse(top._hover_target)

        top.on_hover(HoverTarget("event_0", event=event, view="main.resource"))
        self.assertFalse(top._hover_target)

        top.on_hover(HoverTarget("event_0", time_range=(0, PS_PER_CYCLE), view="main.resource"))
        self.assertFalse(top._hover_target)

        top.on_hover(HoverTarget("event_0", time_range=(0, 2 * PS_PER_CYCLE), view="main.resource"))
        self.assertTrue(top._hover_target.is_time_range())

    def test_hover_command_disables_multiple_event_hover(self):
        top = self.loaded_top()
        self.move_mouse_to_cell(top, (20, 4))

        self.assertTrue(top.command("hover multiple=no"))
        self.assertTrue(top.single_event_hover_enabled)
        self.assertFalse(top.multiple_event_hover_enabled)

        top.on_hover(HoverTarget("event_0", time_range=(0, 2 * PS_PER_CYCLE), view="main.resource"))
        self.assertFalse(top._hover_target)

        event = self.first_event("event_0")
        top.on_hover(HoverTarget("event_0", event=event, view="main.resource"))
        self.assertTrue(top._hover_target.is_event())

    def test_status_bar_omits_hover_tracking_state(self):
        top = self.loaded_top()

        canvas = top.render((120, 40), True)
        text = self.canvas_text(canvas.text)

        self.assertNotIn("hover:armed", text)
        self.assertNotIn("hover:on", text)

        top.mouse_event((120, 40), "mouse drag", 4, 0, 0, True)
        canvas = top.render((120, 40), True)
        text = self.canvas_text(canvas.text)

        self.assertNotIn("hover:armed", text)
        self.assertNotIn("hover:on", text)

    def test_hover_popup_single_event_contains_data(self):
        top = self.loaded_top()
        self.move_mouse_to_cell(top, (20, 4))
        event = self.first_event("event_0")

        top.on_hover(HoverTarget("event_0", event=event, view="main.resource"))
        canvas = top.render((120, 40), True)
        text = self.canvas_text(canvas.text)

        self.assertIn(event.abbrev, text)
        self.assertIn("event.value: 0", text)
        self.assertNotIn("row: event_0", text)
        self.assertNotIn(event.name, top._hover_popup_lines())
        self.assertNotIn(f"hover event_0: {event.abbrev}", text)

    def test_hover_popup_single_event_colors_data_names_like_buttons(self):
        canvas = HoverPopup(["time: 100 ps", "event.value: 1"], bold_labels=True).render((24, 4), True)
        rows = list(canvas.content())

        self.assertEqual("hover_popup_label", rows[1][1][0])
        self.assertEqual(b"time:", rows[1][1][2])
        self.assertEqual("hover_popup_label", rows[2][1][0])
        self.assertEqual(b"event.value:", rows[2][1][2])

    def test_hover_popup_uses_rounded_line_box(self):
        canvas = HoverPopup(["time: 100 ps"]).render((16, 3), True)
        lines = [line.decode() for line in canvas.text]

        self.assertEqual("╭", lines[0][0])
        self.assertEqual("╮", lines[0][-1])
        self.assertEqual("╰", lines[-1][0])
        self.assertEqual("╯", lines[-1][-1])

    def test_hover_popup_range_colors_abbreviations_like_buttons(self):
        canvas = HoverPopup(["e0: 2 (100.00%)"], bold_labels=True).render((24, 3), True)
        row = list(canvas.content())[1]

        self.assertEqual("hover_popup_label", row[1][0])
        self.assertEqual(b"e0:", row[1][2])

    def test_hover_popup_label_matches_button_style(self):
        palette_by_name = {entry[0]: entry[1:] for entry in palette}

        self.assertEqual(palette_by_name["button"], palette_by_name["hover_popup_label"])

    def test_hover_popup_omits_visible_abbrev(self):
        top = self.loaded_top()
        event = self.first_event("event_0")

        top.on_hover(HoverTarget("event_0", event=event, view="main.resource", abbrev_visible=True))
        lines = top._hover_popup_lines()

        self.assertNotIn(f"abbrev: {event.abbrev}", lines)
        self.assertNotIn("row: event_0", lines)
        self.assertEqual(f"time: {event.time // PS_PER_CYCLE:,} cyc", lines[0])
        self.assertNotIn("ps", lines[0])

    def test_hover_popup_transaction_single_event_starts_with_name(self):
        top = self.loaded_top()
        event = self.first_event("event_0")

        top.on_hover(HoverTarget(1, event=event, within_transaction=True, view="main.transaction"))
        lines = top._hover_popup_lines()

        self.assertEqual(f"name: {event.name}", lines[0])
        self.assertIn(f"abbrev: {event.abbrev}", lines)
        self.assertIn(f"time: {event.time // PS_PER_CYCLE:,} cyc", lines)

    def test_hover_popup_range_contains_histogram(self):
        top = self.loaded_top()
        self.move_mouse_to_cell(top, (20, 4))

        top.on_hover(HoverTarget("event_0", time_range=(0, 2 * PS_PER_CYCLE), view="main.resource"))
        canvas = top.render((120, 40), True)
        text = self.canvas_text(canvas.text)

        self.assertNotIn("Summary of", text)
        self.assertNotIn("abbreviation", text)
        abbrev = self.first_event("event_0").abbrev
        self.assertIn(f"{abbrev}: 2 (100.00%)", text)
        self.assertTrue(
            any(
                attr == "hover_popup_label" and segment == f"{abbrev}:".encode()
                for row in canvas.content()
                for attr, _cs, segment in row
            )
        )

    def test_hover_with_selection_uses_popup(self):
        top = self.loaded_top()
        selected_event = self.first_event("event_0")
        hover_event = self.first_event("event_1")

        top.make_selection(selected_event)
        self.move_mouse_to_cell(top, (20, 4))
        top.on_hover(HoverTarget("event_1", event=hover_event, view="main.resource"))
        canvas = top.render((120, 40), True)
        text = self.canvas_text(canvas.text)

        self.assertIs(top.event_details, top.columns.contents[1][0])
        self.assertNotIn("row: event_1", text)
        self.assertIn(f"abbrev: {hover_event.abbrev}", text)
        self.assertNotIn("hover event_1", text)

    def test_hover_popup_prefers_away_from_sidebar(self):
        top = self.loaded_top()
        top.make_selection(self.first_event("event_0"))
        view_right = 120 - top.sidebar_width - 1
        top._hover_cell = (view_right - 1, 4)

        left, _top = top._hover_popup_position(20, 5, (120, 40))

        self.assertLess(left, top._hover_cell[0])
        self.assertLessEqual(left + 20, view_right)

    def test_event_detail_data_text_bolds_data_name(self):
        canvas = EventDetailDataText("event.value", "42", 0, 11).render((20,), False)
        row = list(canvas.content())[0]

        self.assertEqual("even_event_row_data_name", row[0][0])
        self.assertEqual(b"event.value:", row[0][2])
        self.assertEqual("even_event_row", row[1][0])
        self.assertIn(b"42", row[1][2])
