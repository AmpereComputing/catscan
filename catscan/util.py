# Copyright (c) 2024 Ampere Computing. All rights reserved.
# SPDX-License-Identifier: BSD-3-Clause

import re
from collections.abc import Iterable
from re import Pattern

from urwid.str_util import calc_width


def even_odd(idx: int) -> str:
    return "even" if idx % 2 == 0 else "odd"


def even_odd_focused(idx: int, focused: bool) -> str:
    return "focused" if focused else even_odd(idx)


def glob_to_pattern(glob_string: str) -> str:
    replacements = [
        (".", "\\."),
        ("*", "[A-Za-z0-9_.]*"),
        ("?", "[A-Za-z0-9_]+"),
        ("#", "[0-9]+"),
        ("!", "^"),
    ]
    pattern_string = glob_string
    for orig, new in replacements:
        pattern_string = pattern_string.replace(orig, new)
    return pattern_string


def hex_args_to_re(hex_args: Iterable[str]) -> Pattern:
    return re.compile("^((" + ")|(".join(hex_args) + "))$")


def str_width(s: str) -> int:
    return calc_width(s, 0, len(s))


def str_chop(to_chop: str, max_width: int) -> str:
    to_chop = to_chop[:max_width]
    overage = str_width(to_chop) - max_width
    while overage > 0:
        safe_removal = max(1, overage // 3)  # max character width is 3
        to_chop = to_chop[:-safe_removal]
        overage = str_width(to_chop) - max_width
    return to_chop


def str_chop_front(to_chop: str, max_width: int) -> str:
    to_chop = to_chop[-max_width:]
    overage = str_width(to_chop) - max_width
    while overage > 0:
        safe_removal = max(1, overage // 3)  # max character width is 3
        to_chop = to_chop[safe_removal:]
        overage = str_width(to_chop) - max_width
    return to_chop


def str_fit_width(
    to_fit: str,
    max_width: int,
    continuation: str | None = "…",
    continuation_min: int = 1,
    continuation_right: bool = True,
    pad: str | None = None,
) -> str:
    """
    Return a string fitted to a particular width, optionally padded (supply pad
    = ['>', '<', '^']) if `to_fit` is narrower than max_width.
    """
    continuation_min = max(continuation_min, str_width(continuation))
    if str_width(to_fit) > max_width:
        if max_width <= continuation_min:
            to_fit = str_chop(to_fit, max_width) if continuation_right else str_chop_front(to_fit, max_width)
        elif continuation_right:
            to_fit = str_chop(to_fit, max_width - str_width(continuation)) + continuation
        else:
            to_fit = continuation + str_chop_front(to_fit, max_width - str_width(continuation))

    if str_width(to_fit) == max_width:
        return to_fit
    if pad:
        adjusted_max_width = max_width + len(to_fit) - str_width(to_fit)
        return f"{to_fit: {pad}{adjusted_max_width}}"
    return to_fit
