# Copyright (c) 2024 Ampere Computing. All rights reserved.
# SPDX-License-Identifier: BSD-3-Clause

# Color definitions based loosely on Solarized color scheme
solarized = {
    # solarized name, urwid color "name"
    "base03": "g3",
    "base02": "g7",
    "base01": "g15",
    "base00": "g23",
    "base13": "g11",
    "base12": "g19",
    "base11": "g27",
    "base0": "g70",
    "base1": "g78",
    "base2": "#dda",
    "base3": "#ddd",
    "yellow": "#b58900",
    "orange": "#f60",
    "red": "#d00",
    "magenta": "#d36",
    "violet": "#66d",
    "blue": "#268bd2",
    "cyan": "#2aa198",
    "green": "#859900",
}

# Define the base of the color palette to be passed to urwid.BaseScreen.register_palette()
palette = [
    # name, foreground, background, terminal settings, foreground_high, background_high
    ("body", "light gray", "black", None, solarized["base0"], solarized["base03"]),
    ("action", "light red", "white", None, solarized["orange"], solarized["base02"]),
    ("even_event_row", "light gray", "black", None, solarized["base0"], solarized["base03"]),
    ("odd_event_row", "light gray", "dark gray", None, solarized["base0"], solarized["base02"]),
    ("even_event_row_active", "light gray", "black", None, solarized["base0"], solarized["base13"]),
    ("odd_event_row_active", "light gray", "dark gray", None, solarized["base0"], solarized["base12"]),
    ("focused_event_row", "light gray", "dark blue", None, solarized["base0"], solarized["base01"]),
    ("focused_event_row_active", "light gray", "dark blue", None, solarized["base0"], solarized["base11"]),
    ("even_group_row", "light cyan", "black", None, solarized["blue"], solarized["base03"]),
    ("odd_group_row", "light cyan", "dark gray", None, solarized["blue"], solarized["base02"]),
    ("focused_group_row", "light cyan", "dark blue", None, solarized["blue"], solarized["base01"]),
    ("blue_text", "light cyan", "black", None, solarized["cyan"], solarized["base03"]),
    ("button", "light cyan", "black", None, "#ffa", solarized["base03"]),
    ("button_even", "light cyan", "black", None, "#ffa", solarized["base03"]),
    ("button_odd", "light cyan", "dark gray", None, "#ffa", solarized["base02"]),
    ("ampere_red_fg", "light red", "black", None, "#f00", "g3"),
]

# Generate a few variants of the same basic colors for even/odd/focused rows
# below, appending those results to the main palette
event_colors = [
    # color index, foreground, background, terminal settings, foreground_high, background_high
    #   note: 'color index' is the number used internally in Catscan to refer
    #   to variants of the display attributes based on each base color, below
    (None, "light gray", "black", None, solarized["base03"], solarized["base00"]),
    (0, "light gray", "brown", None, solarized["base01"], solarized["yellow"]),
    (1, "light gray", "light red", None, solarized["base01"], solarized["orange"]),
    (2, "light gray", "dark red", None, solarized["base01"], solarized["red"]),
    (3, "light gray", "dark magenta", None, solarized["base01"], solarized["magenta"]),
    (4, "light gray", "light magenta", None, solarized["base01"], solarized["violet"]),
    (5, "light gray", "dark blue", None, solarized["base01"], solarized["blue"]),
    (6, "light gray", "dark cyan", None, solarized["base01"], solarized["cyan"]),
    (7, "light gray", "dark green", None, solarized["base01"], solarized["green"]),
]
for color in event_colors:
    palette.append((f"event_color_{color[0]}",) + color[1:])
    palette.append((f"event_color_reversed_{color[0]}_even", color[2], "black", None, color[5], solarized["base03"]))
    palette.append((f"event_color_reversed_{color[0]}_odd", color[2], "dark gray", None, color[5], solarized["base02"]))
    palette.append(
        (f"event_color_reversed_{color[0]}_even_active", color[2], "black", None, color[5], solarized["base13"])
    )
    palette.append(
        (f"event_color_reversed_{color[0]}_odd_active", color[2], "dark gray", None, color[5], solarized["base12"])
    )
    palette.append(
        (f"event_color_reversed_{color[0]}_focused", color[2], "dark blue", None, color[5], solarized["base01"])
    )
    palette.append(
        (f"event_color_reversed_{color[0]}_focused_active", color[2], "dark blue", None, color[5], solarized["base11"])
    )
