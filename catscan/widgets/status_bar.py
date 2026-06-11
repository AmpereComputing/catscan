# Copyright (c) 2024 Ampere Computing. All rights reserved.
# SPDX-License-Identifier: BSD-3-Clause

from abc import ABC, abstractmethod
from collections.abc import Callable

import urwid

from catscan.commands import CommandCompletion, Commands, completable_command_definitions
from catscan.completion import FilteredSuggestions, HistoricalCompletion
from catscan.state import CatscanState
from catscan.user_input import ACTIONS, action_keypresses
from catscan.util import str_fit_width, str_width
from catscan.widgets.separators import BottomBorder


class StatusText(urwid.widget.Widget):
    _sizing = frozenset(["flow"])
    _selectable = False

    def __init__(self, initial_status_text: str, initial_source_text: str | None) -> None:
        self.set_text(initial_status_text, initial_source_text)
        super().__init__()

    def set_text(self, status_text: str, source_text: str | None) -> None:
        self.status_text = status_text
        self.source_text = source_text
        self._invalidate()

    def rows(self, size: tuple[int], focus: bool = False) -> int:
        return 1

    def render(
        self,
        size: tuple[()] | tuple[int] | tuple[int, int],
        focus: bool = False,
    ) -> urwid.canvas.Canvas:
        (maxcol,) = size

        text = str_fit_width(self.status_text, maxcol)
        remaining = maxcol - str_width(text)

        # Don't try to print filename if there are fewer than 10 characters
        # remaining in the row
        if remaining < 10 or self.source_text is None:
            text += " " * remaining
        else:
            text += str_fit_width(self.source_text, remaining, continuation="-", continuation_right=False, pad=">")

        return urwid.canvas.TextCanvas([text.encode()])


class StatusBar(urwid.WidgetWrap):
    """
    Display a status bar at the bottom of the screen, allowing the user to type
    commands/searches as required.
    """

    class Mode:
        """Status-bar mode."""

        def __init__(
            self,
            prefix: str,
            event: Callable,
            history: FilteredSuggestions | None = None,
            completion: FilteredSuggestions | None = None,
        ):
            self.completion_event = event
            self.box = urwid.Edit(prefix, "")
            self._history_prefix = "" if history is None else prefix
            self.history = history or HistoricalCompletion()
            self.completion = completion

        def enter(self, parent: "StatusBar"):
            parent._set_bar_widget(self.box)
            self.box.edit_text = ""

        @property
        def _text_with_prefix(self) -> str:
            return self._history_prefix + self.box.edit_text

        def exit(self) -> str:
            text = self.box.edit_text
            if text.strip():
                self.history.add(self._text_with_prefix)
            self.history.reset()
            if self.completion is not None:
                self.completion.reset()
            return text

        def _try_assign_text(self, text: str | None, position: int | None = None):
            if text is not None:
                self.box.edit_text = text.removeprefix(self._history_prefix)
                if position is not None:
                    if position < 0:
                        position = len(self.box.edit_text)
                    else:
                        position -= len(text) - len(self.box.edit_text)
                    self.box.set_edit_pos(position)

        def back_in_history(self):
            self._try_assign_text(self.history.back(self._text_with_prefix), -1)

        def forward_in_history(self):
            self._try_assign_text(self.history.forward(), -1)

        def back_in_suggestions(self):
            if self.completion is not None:
                self._try_assign_text(*self.completion.back_with_position(self.box.edit_text, self.box.edit_pos))

        def forward_in_suggestions(self):
            if self.completion is not None:
                self._try_assign_text(*self.completion.forward_with_position())

        def reset_suggestions(self):
            self.history.reset(self._text_with_prefix)
            if self.completion is not None and (adjusted_text := self.completion.reset(self.box.edit_text)):
                self._try_assign_text(adjusted_text)

        def go_to_start(self):
            self.reset_suggestions()
            self.box.set_edit_pos(0)

        def go_to_end(self):
            self.reset_suggestions()
            self.box.set_edit_pos(len(self.box.edit_text))

    def __init__(
        self,
        state: CatscanState,
        on_search: Callable,
        on_command: Callable,
        initial_status_text: str = "Loading...",
        initial_source_text: str | None = None,
    ) -> None:
        self.state = state
        self._sidebar_width = None
        self._showing_scrollbar = False

        # True if user is actively entering a search query or command
        self.mode = "status"
        self.history = HistoricalCompletion()
        self.modes = {
            "search": StatusBar.Mode(
                f":{Commands.SEARCH.lower()} ",
                on_search,
                history=self.history,
                completion=CommandCompletion(completable_command_definitions[Commands.SEARCH]),
            ),
            "command": StatusBar.Mode(
                ":",
                on_command,
                history=self.history,
                completion=CommandCompletion(completable_command_definitions),
            ),
        }

        self.bottom_border = BottomBorder(self.state)
        self.text_box = StatusText(initial_status_text, initial_source_text)
        self.pile = urwid.Pile([self.bottom_border, self.text_box], focus_item=1)

        super().__init__(self.pile)

    @property
    def sidebar_width(self) -> int | None:
        return self._sidebar_width

    @sidebar_width.setter
    def sidebar_width(self, value: int | None) -> None:
        self._sidebar_width = value
        self.bottom_border.sidebar_width = value

    @property
    def showing_scrollbar(self) -> bool:
        return self._showing_scrollbar

    @showing_scrollbar.setter
    def showing_scrollbar(self, value: bool) -> None:
        self._showing_scrollbar = value
        self.bottom_border.showing_scrollbar = value

    def update_state(self, new_state: CatscanState) -> None:
        if new_state != self.state:
            self.state = new_state
            self.bottom_border.update_state(new_state)
            self._invalidate()

    def _set_bar_widget(self, widget: urwid.Widget) -> None:
        self._w.contents[1] = (widget, ((urwid.WHSettings.WEIGHT, 1)))

    def set_text(self, status_text: str, source_text: str | None) -> None:
        self.text_box.set_text(status_text, source_text)

    def _reset(self) -> None:
        self.mode = "status"
        self._set_bar_widget(self.text_box)

    def cancel_operation(self) -> None:
        self._reset()

    def _enter_mode(self, mode: str) -> None:
        self.mode = mode
        if mode in self.modes:
            self.modes[mode].enter(self)

    def _exit_mode(self) -> tuple[Mode, str]:
        mode = self.modes[self.mode]
        text = mode.exit()
        self._reset()
        return mode, text

    def enter_search(self) -> None:
        self._enter_mode("search")

    def enter_command(self) -> None:
        self._enter_mode("command")

    def keypress(
        self,
        size: tuple[()] | tuple[int] | tuple[int, int],
        key: str,
    ) -> str | None:
        ret = self._w.keypress(size, key)

        if self.mode in self.modes:
            status_mode = self.modes[self.mode]
            if ret in action_keypresses[ACTIONS.STATUS_BAR_COMPLETION]:
                mode, text = self._exit_mode()
                mode.completion_event(text)
            elif ret in action_keypresses[ACTIONS.STATUS_BAR_HISTORY_BACK]:
                status_mode.back_in_history()
            elif ret in action_keypresses[ACTIONS.STATUS_BAR_HISTORY_FORWARD]:
                status_mode.forward_in_history()
            elif ret in action_keypresses[ACTIONS.STATUS_BAR_SUGGESTION_BACK]:
                status_mode.back_in_suggestions()
            elif ret in action_keypresses[ACTIONS.STATUS_BAR_SUGGESTION_FORWARD]:
                status_mode.forward_in_suggestions()
            elif ret in action_keypresses[ACTIONS.STATUS_BAR_GOTO_START]:
                status_mode.go_to_start()
            elif ret in action_keypresses[ACTIONS.STATUS_BAR_GOTO_END]:
                status_mode.go_to_end()
            elif ret is None and key not in ("left", "right"):
                status_mode.reset_suggestions()

        return ret
