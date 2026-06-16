# Copyright (c) 2024-2025 Ampere Computing. All rights reserved.
# SPDX-License-Identifier: BSD-3-Clause

from typing import Any, Literal

import urwid
import urwid.canvas


class FixedWidthScrollBar(urwid.ScrollBar):
    """Scrollbar which always shows a border (trough character)."""

    def __init__(
        self,
        widget: urwid.Widget,
        *args: Any,
        trough_char: str = " ",
        side: Literal["left", "right"] = "right",
        width: int = 1,
        **kwargs: Any,
    ):
        self._border_char = trough_char
        self._border_width = width
        self._border_side = side

        super().__init__(widget, *args, trough_char=trough_char, side=side, width=width, **kwargs)

    def render(self, size: tuple[int, int], focus: bool = False) -> urwid.canvas.Canvas:
        maxcol, maxrow = size
        rows_max = self.scrolling_base_widget.rows_max(size, focus)
        if rows_max > maxrow:
            # Scrollbar will show, use normal rendering
            return super().render(size, focus)

        # Scrollbar will not show, so add padding of trough character
        content_width = maxcol - self._border_width
        canvas = super().render((content_width, maxrow), focus)
        border_canvas = urwid.canvas.SolidCanvas(self._border_char, self._border_width, maxrow)
        combinelist = [
            (canvas, None, True, content_width),
            (border_canvas, None, False, self._border_width),
        ]

        if self._border_side != "left":
            return urwid.canvas.CanvasJoin(combinelist)
        return urwid.canvas.CanvasJoin(reversed(combinelist))
