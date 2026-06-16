# Copyright (c) 2024-2025 Ampere Computing. All rights reserved.
# SPDX-License-Identifier: BSD-3-Clause

from typing import Any

import urwid


class Release(urwid.WidgetWrap):
    """Transform button-like object to respond to event release instead of press."""

    def __init__(self, button: urwid.Widget, allow_other_press: bool = False):
        self._allow_other_press = allow_other_press
        super().__init__(button)

    def mouse_event(
        self,
        size: tuple[()] | tuple[int] | tuple[int, int],
        event: str,
        button: int,
        col: int,
        row: int,
        focus: bool,
    ) -> bool | None:
        # Intercept the event as it "would" normally be handled, prevents other behavior occuring
        if event == "mouse press":
            return not self._allow_other_press
        # Transform mouse release event into mouse press so that button-like objects respond to
        # a "release" instead of "press"
        if event == "mouse release":
            event = "mouse press"

        return self._w.mouse_event(size, event, button, col, row, focus)


class UnpaddedButton(urwid.Button):
    """Button without padding and '</>' characters."""

    def __init__(self, label: str, *args: Any, **kwargs: Any):
        super().__init__(label, *args, **kwargs)
        super(urwid.Button, self).__init__(urwid.Columns([self._label]))


def ReleaseButton(*args: Any, allow_other_press: bool = False, **kwargs: Any) -> Release:  # noqa: N802
    """Create a button that listens on mouse release."""
    return Release(urwid.Button(*args, **kwargs), allow_other_press=allow_other_press)


def ReleaseUnpaddedButton(*args: Any, allow_other_press: bool = False, **kwargs: Any) -> Release:  # noqa: N802
    """Create a button that listens on mouse release."""
    return Release(UnpaddedButton(*args, **kwargs), allow_other_press=allow_other_press)


def ReleaseCheckBox(*args: Any, allow_other_press: bool = False, **kwargs: Any) -> Release:  # noqa: N802
    """Create a checkbox that listens on mouse release."""
    return Release(urwid.CheckBox(*args, **kwargs), allow_other_press=allow_other_press)
