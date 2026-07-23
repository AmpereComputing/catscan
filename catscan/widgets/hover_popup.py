# Copyright (c) 2024 Ampere Computing. All rights reserved.
# SPDX-License-Identifier: BSD-3-Clause

from collections.abc import Sequence

import urwid

from catscan.util import str_fit_width, str_width


class _HoverPopupBody(urwid.widget.Widget):
    """Text body for a hover popup."""

    _sizing = frozenset(["box"])
    _selectable = False
    LABEL_ATTR = "hover_popup_label"

    def __init__(self, lines: Sequence[str], bold_labels: bool = False) -> None:
        self.lines = list(lines)
        self.bold_labels = bold_labels
        super().__init__()

    def _row_attrs(self, line: str, inner_width: int) -> tuple[bytes, list[tuple[str | None, int]]]:
        fitted = str_fit_width(line, inner_width)
        padding = " " * (inner_width - str_width(fitted))
        row = fitted + padding

        if not self.bold_labels or ":" not in fitted:
            return row.encode(), [(None, len(row.encode()))]

        label_end = fitted.index(":") + 1
        return row.encode(), [
            (self.LABEL_ATTR, len(fitted[:label_end].encode())),
            (None, len((fitted[label_end:] + padding).encode())),
        ]

    def render(
        self,
        size: tuple[()] | tuple[int] | tuple[int, int],
        focus: bool = False,
    ) -> urwid.canvas.Canvas:
        maxcol, maxrow = size
        if maxcol <= 0 or maxrow <= 0:
            return urwid.canvas.TextCanvas([(" " * maxcol).encode()] * maxrow)

        rows = []
        attrs = []
        for line in self.lines[:maxrow]:
            row, row_attrs = self._row_attrs(line, maxcol)
            rows.append(row)
            attrs.append(row_attrs)

        while len(rows) < maxrow:
            row = (" " * maxcol).encode()
            rows.append(row)
            attrs.append([(None, len(row))])

        return urwid.canvas.TextCanvas(rows, attrs)


class HoverPopup(urwid.WidgetWrap):
    """Small hover popup rendered as a boxed text canvas."""

    _selectable = False
    MAX_WIDTH = 72
    MAX_HEIGHT = 16

    def __init__(self, lines: Sequence[str], bold_labels: bool = False) -> None:
        self.lines = list(lines)
        self.bold_labels = bold_labels
        symbols = urwid.LineBox.Symbols.LIGHT
        body = _HoverPopupBody(self.lines, bold_labels)
        super().__init__(
            urwid.LineBox(
                body,
                tlcorner=symbols.TOP_LEFT_ROUNDED,
                trcorner=symbols.TOP_RIGHT_ROUNDED,
                blcorner=symbols.BOTTOM_LEFT_ROUNDED,
                brcorner=symbols.BOTTOM_RIGHT_ROUNDED,
            )
        )

    @property
    def desired_width(self) -> int:
        content_width = max([str_width(line) for line in self.lines] or [0])
        return max(4, min(content_width + 2, self.MAX_WIDTH))

    @property
    def desired_height(self) -> int:
        return max(3, min(len(self.lines) + 2, self.MAX_HEIGHT))

    def render(
        self,
        size: tuple[()] | tuple[int] | tuple[int, int],
        focus: bool = False,
    ) -> urwid.canvas.Canvas:
        maxcol, maxrow = size
        if maxcol < 2 or maxrow < 2:
            return urwid.canvas.TextCanvas([(" " * maxcol).encode()] * maxrow)
        return super().render(size, focus)
