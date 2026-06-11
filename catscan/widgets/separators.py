# Copyright (c) 2024 Ampere Computing. All rights reserved.
# SPDX-License-Identifier: BSD-3-Clause

import urwid

from catscan.state import CatscanState


class HorizontalBorder(urwid.widget.Widget):
    _sizing = frozenset(["flow"])
    _selectable = False

    def __init__(
        self,
        state: CatscanState,
        border_char: str = "─",
        right_char: str = "┘",
        middle_char: str = "┴",
        within_view: bool = False,
    ) -> None:
        self.state = state

        self.border_char = border_char
        self.right_char = right_char
        self.middle_char = middle_char

        self.sidebar_width = None
        self.showing_scrollbar = within_view
        super().__init__()

    def update_state(self, new_state: CatscanState) -> None:
        if new_state == self.state:
            return

        self.state = new_state
        self._invalidate()

    def rows(self, size: tuple[int], focus: bool = False) -> int:
        return 1

    def render(
        self,
        size: tuple[()] | tuple[int] | tuple[int, int],
        focus: bool = False,
    ) -> urwid.canvas.Canvas:
        (maxcol,) = size

        if self.state.loading:
            border_row = self.border_char * maxcol
        else:
            border_row = self.border_char * (self.state.column_header_width - 1) + self.middle_char
            if self.sidebar_width is not None:
                non_sidebar_remaining = maxcol - self.state.column_header_width - self.sidebar_width - 2
                border_row += self.border_char * non_sidebar_remaining + self.middle_char
            border_row += self.border_char * (maxcol - 1 - len(border_row)) + (
                self.right_char if self.showing_scrollbar else self.border_char
            )
        return urwid.canvas.TextCanvas([border_row.encode()])


class BottomBorder(HorizontalBorder):
    """Border for bottom, spanning all view columns."""


class MiddleBorder(HorizontalBorder):
    """Border for middle, within views."""

    def __init__(self, state: CatscanState):
        super().__init__(state, middle_char="┼", right_char="┤", within_view=True)
