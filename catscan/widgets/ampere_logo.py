# Copyright (c) 2024 Ampere Computing. All rights reserved.
# SPDX-License-Identifier: BSD-3-Clause

import math
from typing import NamedTuple

import urwid

Coord = tuple[float, float]
Circle = tuple[float, float, float]


class AmpereLogo(urwid.widget.Widget):
    """
    A widget which displays an ASCII version of the Ampere Logo, scaled and
    centered within the available space (as passed into the 'row' and 'render'
    methods by the containing Widget), with the 'C' and 'A' portions of the
    logo translated by an amount varying by how much of a file is remaining to
    be loaded/processed.
    """

    _sizing = frozenset(["box"])
    _selectable = False

    class AmpereLogoFormulas(NamedTuple):
        # line format: (m, b) in `y=m*x + b`
        left_outside_line: Coord
        left_inside_line: Coord
        left_inside_gap_line: Coord
        right_inside_line: Coord
        right_outside_line: Coord
        bottom_line: Coord
        # circle format: (cx, cy, r) where cx and cy are the coordinates of the
        inner_circle: Circle
        outer_circle: Circle

    # Set of formulas which, when combined, circumscribe the Ampere logo
    FORMULAS = AmpereLogoFormulas(
        # line format: (m, b) in `y=m*x + b`
        left_outside_line=(1.863400, -3426.116484),
        left_inside_line=(1.863400, -3557.335030),
        left_inside_gap_line=(1.863400, -3725.138499),
        right_inside_line=(-1.863400, 3367.598517),
        right_outside_line=(-1.863400, 3493.067373),
        bottom_line=(0, -748.40132),
        # circle format: (cx, cy, r) where cx and cy are the coordinates of the
        # center and r is the radius
        inner_circle=(1696.4403, -1068.3503, 603.54993),
        outer_circle=(1695.8824, -1066.5382, 661.58331),
    )

    def __init__(self) -> None:
        self.pct_loaded = 0.0
        super().__init__()

    def translate(self, orig_coords: Coord, by: Coord) -> Coord:
        x, y = orig_coords
        by_x, by_y = by
        return (x + by_x, y + by_y)

    def translate_mxb(self, orig_mxb: Coord, by: Coord) -> Coord:
        m, b = orig_mxb
        new_x, new_y = self.translate((0, b), by)
        new_b = new_y - m * new_x
        return (m, new_b)

    def scale(self, orig_coords: Coord, by: float, around: Coord = (0, 0)) -> Coord:
        ax, ay = around
        translated = self.translate(orig_coords, (-ax, -ay))
        tx, ty = translated
        new_coords = (tx * by, ty * by)
        return self.translate(new_coords, around)

    def scale_mxb(self, orig_mxb: Coord, by: float, around: Coord = (0, 0)) -> Coord:
        m, b = orig_mxb
        new_x, new_y = self.scale((0, b), by, around)
        new_b = new_y - m * new_x
        return (m, new_b)

    def under_line(self, coords: Coord, line: Coord) -> bool:
        m, b = line
        x, y = coords
        return y < (m * x + b)

    def inside_circle(self, coords: Coord, circle: (float, float, float)) -> bool:
        cx, cy, r = circle
        x, y = self.translate(coords, (-cx, -cy))
        return math.hypot(x, y) < r

    def intersection(self, line_a: Coord, line_b: Coord) -> Coord:
        am, ab = line_a
        bm, bb = line_b
        x = (ab - bb) / (bm - am)
        y = (am * x) + ab
        return (x, y)

    def in_ampere_logo(self, formulas: AmpereLogoFormulas, coords: Coord) -> bool:
        if self.under_line(coords, formulas.bottom_line):
            return False
        # The 'tent' portion of the A
        if (
            self.under_line(coords, formulas.left_outside_line)
            and self.under_line(coords, formulas.right_outside_line)
            and (
                not self.under_line(coords, formulas.left_inside_line)
                or not self.under_line(coords, formulas.right_inside_line)
            )
        ):
            return True
        # The semi-circular portion of the logo
        return (
            self.inside_circle(coords, formulas.outer_circle)
            and not self.inside_circle(coords, formulas.inner_circle)
            and (
                self.under_line(coords, formulas.left_inside_gap_line)
                or not self.under_line(coords, formulas.left_inside_line)
            )
        )

    def get_bounding_box(self, formulas: AmpereLogoFormulas) -> tuple[Coord, Coord]:
        top = self.intersection(formulas.left_outside_line, formulas.right_outside_line)
        max_y = top[1]
        min_y = formulas.bottom_line[1]
        cx, cy, r = formulas.outer_circle
        x_diff = math.sqrt(r**2 - (min_y - cy) ** 2)
        min_x = cx - x_diff
        max_x = cx + x_diff

        return ((min_x, min_y), (max_x, max_y))

    def recalc(
        self, max_xrange: float, max_yrange: float, translate_lines: Coord = (0, 0), translate_circles: Coord = (0, 0)
    ) -> "AmpereLogo.AmpereLogoFormulas":
        # Find the bounding box of the 'master' formula
        ((min_x, min_y), (max_x, max_y)) = self.get_bounding_box(AmpereLogo.FORMULAS)

        pre_translate_by = (-min_x, -min_y)
        scale_by = min(max_xrange / (max_x - min_x), max_yrange / (max_y - min_y))
        post_translate_by = (
            (max_xrange - (max_x - min_x) * scale_by) / 2,
            (max_yrange - ((max_y - min_y) * scale_by)) / 2,
        )

        def change_line(prev: Coord, reverse_extra_y: bool = False) -> Coord:
            new_line = self.translate_mxb(prev, pre_translate_by)
            new_line = self.scale_mxb(new_line, scale_by)
            new_line = self.translate_mxb(new_line, post_translate_by)
            if reverse_extra_y:
                return self.translate_mxb(new_line, (translate_lines[0], -translate_lines[1]))
            return self.translate_mxb(new_line, translate_lines)

        def change_circle(prev: Circle) -> Circle:
            cx, cy, r = prev
            new_center = self.translate((cx, cy), pre_translate_by)
            new_center = self.scale(new_center, scale_by)
            new_center = self.translate(new_center, post_translate_by)
            new_center = self.translate(new_center, translate_circles)
            r = r * scale_by
            return (new_center[0], new_center[1], r)

        return AmpereLogo.AmpereLogoFormulas(
            left_outside_line=change_line(AmpereLogo.FORMULAS.left_outside_line),
            left_inside_line=change_line(AmpereLogo.FORMULAS.left_inside_line),
            left_inside_gap_line=change_line(AmpereLogo.FORMULAS.left_inside_gap_line),
            right_inside_line=change_line(AmpereLogo.FORMULAS.right_inside_line),
            right_outside_line=change_line(AmpereLogo.FORMULAS.right_outside_line),
            bottom_line=change_line(AmpereLogo.FORMULAS.bottom_line, reverse_extra_y=True),
            inner_circle=change_circle(AmpereLogo.FORMULAS.inner_circle),
            outer_circle=change_circle(AmpereLogo.FORMULAS.outer_circle),
        )

    def update_pct_loaded(self, pct_loaded: float):
        self.pct_loaded = pct_loaded
        self._invalidate()

    def render(
        self,
        size: tuple[()] | tuple[int] | tuple[int, int],
        focus: bool = False,
    ) -> urwid.canvas.Canvas:
        (maxcol, maxrow) = size

        # Determine the translation vectors for the lines and circles, based
        # how complete the loading process is
        translate_lines = (0, maxrow * 4 * (100 - self.pct_loaded) / 100)
        translate_circles = (-maxcol * (100 - self.pct_loaded) / 100, 0)

        # Re-calculate the formulas so they correspond to the actual size of
        # our current viewport (pass maxrow*2 for yrange because terminal
        # characters are generally 2x as high as they are wide)
        formulas = self.recalc(maxcol, maxrow * 2, translate_lines=translate_lines, translate_circles=translate_circles)

        # For every character inside the viewport, determine if it is 'inside'
        # the current formula set. If it is, display the appropriate character
        # within 'AmpereComputing'. If it is not, display a space.
        rows = []
        logo_characters = "AmpereComputing"
        for y in range(2 * (maxrow - 1), -1, -2):
            row = ""
            for x in range(maxcol):
                if self.in_ampere_logo(formulas, (x, y)):
                    letter_idx = (x + y) % len(logo_characters)
                    row += logo_characters[letter_idx]
                else:
                    row += " "
            rows.append(bytes(row, "utf-8"))

        attr = [[("ampere_red_fg", len(r))] for r in rows]
        return urwid.canvas.TextCanvas(rows, attr)
