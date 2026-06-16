# Copyright (c) 2024 Ampere Computing. All rights reserved.
# SPDX-License-Identifier: BSD-3-Clause

from collections.abc import Callable
from typing import NamedTuple

import urwid

from catscan.commands import command_definitions
from catscan.state import CatscanState
from catscan.user_input import ACTIONS, action_keypresses, action_mouseevents_pretranslated
from catscan.widgets.button import ReleaseButton


class PopupCoords(NamedTuple):
    """
    Represents the calculated coordinates of a Popup Widget.
    """

    left: int
    top: int
    width: int
    height: int

    @classmethod
    def from_limits(cls, max_height: int, max_width: int, available_height: int, available_width: int) -> "PopupCoords":
        popup_width = min(max_width, available_width)
        popup_height = min(max_height, available_height)
        return cls(
            left=(available_width - popup_width) // 2,
            top=(available_height - popup_height) // 2,
            width=popup_width,
            height=popup_height,
        )

    @property
    def right(self) -> int:
        return self.left + self.width

    @property
    def bottom(self) -> int:
        return self.top + self.height

    @property
    def size(self) -> tuple[int, int]:
        return (self.width, self.height)

    def is_within(self, col: int, row: int) -> bool:
        return col >= self.left and col <= self.right and row >= self.top and row <= self.bottom


class ClosablePopup(urwid.WidgetWrap):
    def __init__(
        self,
        contents: urwid.Widget,
        title: str,
        on_close: Callable,
        desired_width: int,
        desired_height: int,
    ) -> None:
        self.on_close = on_close
        self.desired_width = desired_width
        self.desired_height = desired_height

        self.close_button = urwid.Padding(
            ReleaseButton("Close", on_press=self.close, align="right"), align="center", width=9
        )
        self.frame = urwid.Frame(contents, header=urwid.Text(""), footer=self.close_button)
        padding = urwid.Padding(self.frame, left=2, right=2)
        self.line_box = urwid.LineBox(padding, title=f" {title} ")

        super().__init__(self.line_box)

    def close(self, _widget, _user_args=None) -> bool:
        return self.on_close()

    def coords(self, size: tuple[int, int]) -> PopupCoords:
        return PopupCoords.from_limits(self.desired_height, self.desired_width, size[1], size[0])

    def overlay(self, canvas: urwid.Canvas, size: tuple[int, int], focus: bool = False) -> urwid.CompositeCanvas:
        coords = self.coords(size)
        overlay_canvas = self.render((coords.width, coords.height), focus)
        canvas = urwid.CompositeCanvas(canvas)
        canvas.overlay(overlay_canvas, left=coords.left, top=coords.top)
        return canvas

    def keypress(
        self,
        size: tuple[()] | tuple[int] | tuple[int, int],
        key: str,
    ) -> str | None:
        key = self._w.keypress(size, key)
        if key is None:
            return None
        if key in action_keypresses[ACTIONS.POPUP_CLOSE] and self.on_close():
            return None
        return key


class Messages(ClosablePopup):
    def __init__(
        self,
        state: CatscanState,
        on_close: Callable,
        desired_width: int,
        desired_height: int,
    ) -> None:
        self.state = state

        self.list_walker = urwid.SimpleFocusListWalker(self.message_widgets)
        self.list_box = urwid.ListBox(self.list_walker)
        self.scrollable = urwid.ScrollBar(self.list_box, trough_char="│")

        super().__init__(
            self.scrollable,
            title="Messages",
            on_close=on_close,
            desired_width=desired_width,
            desired_height=desired_height,
        )

    @property
    def message_widgets(self) -> list[urwid.Text]:
        return [urwid.Text(message) for message in self.state.messages]

    def update_state(self, new_state: CatscanState) -> None:
        if new_state == self.state:
            return

        self.state = new_state
        self.list_walker.clear()
        self.list_walker += self.message_widgets
        self._invalidate()


class Help(ClosablePopup):
    def __init__(
        self,
        on_close: Callable,
        desired_width: int,
        desired_height: int,
    ) -> None:
        self._command_filter = None
        self.list_walker = urwid.SimpleFocusListWalker([])
        self.list_box = urwid.ListBox(self.list_walker)
        self.scrollable = urwid.ScrollBar(self.list_box, trough_char="│")
        self.update()

        super().__init__(
            self.scrollable,
            title="Help / Input Mappings",
            on_close=on_close,
            desired_width=desired_width,
            desired_height=desired_height,
        )

    def filter_by_command(self, command: str):
        old = self._command_filter
        self._command_filter = command
        if old != self._command_filter:
            self.update()

    def reset_filter(self):
        old = self._command_filter
        self._command_filter = None
        if old != self._command_filter:
            self.update()

    def update(self):
        self.list_walker.clear()
        if not self._command_filter:
            self.list_walker.append(
                urwid.AttrMap(urwid.Text("Keyboard/Mouse Mappings", align="center"), "focused_event_row")
            )
            self.list_walker.append(urwid.Text(""))
            for action in ACTIONS:
                self.list_walker.append(urwid.Text(f"{action}:"))
                user_inputs = action_keypresses.get(action, ()) + action_mouseevents_pretranslated.get(action, ())
                input_text = [t for user_input in user_inputs for t in (("action", user_input), ", ")][:-1]
                self.list_walker.append(urwid.Padding(urwid.Text(input_text), left=2))

            self.list_walker.append(urwid.Text(""))
            self.list_walker.append(urwid.AttrMap(urwid.Text("Commands", align="center"), "focused_event_row"))
            self.list_walker.append(urwid.Text(""))

        for name, cmd in command_definitions.items():
            if self._command_filter and name != self._command_filter:
                continue

            self.list_walker.append(urwid.Text(("action", f"{cmd.signature}")))
            if cmd.has_example:
                self.list_walker.append(urwid.Padding(urwid.Text(f"{cmd.description}, for example:"), left=2))
                self.list_walker.append(urwid.Padding(urwid.Text(f"{cmd.example}"), left=4))
            else:
                self.list_walker.append(urwid.Padding(urwid.Text(f"{cmd.description}"), left=2))
