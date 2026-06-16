# Copyright (c) 2024 Ampere Computing. All rights reserved.
# SPDX-License-Identifier: BSD-3-Clause

from collections.abc import Sequence

import urwid

from catscan.util import even_odd


class TextTable(urwid.widget.Widget):
    """
    Given a table (in the form of lists of strings, like what is returned by
    generate_summary_table()), render that table as a 'flow' widget.
    """

    _sizing = frozenset(["flow"])
    _selectable = False

    LINE_CONTINUATION = "…"
    ROW_DIVIDER = "─"
    COL_DIVIDER = " │ "
    ROW_COL_DIVIDER = "─┼─"
    INDENTATION = "  "

    def __init__(
        self,
        header: Sequence[str] | None = None,
        contents: Sequence[Sequence[str]] | None = None,
        footer: Sequence[str] | None = None,
        title: str | None = None,
        align: urwid.Align = urwid.CENTER,
    ) -> None:
        self.align = align
        self.set_contents(header, contents, footer, title)
        super().__init__()

    def set_contents(
        self,
        header: Sequence[str] | None = None,
        contents: Sequence[Sequence[str]] | None = None,
        footer: Sequence[str] | None = None,
        title: str | None = None,
    ) -> None:
        self.header = header or []
        self.footer = footer or []
        self.contents = contents or []
        self.title = title
        self.num_rows = len(self.contents)
        self.num_columns = len(self.contents[0]) if self.num_rows else 0

        # Make sure everything has the same number of columns (though the
        # header/footer are allowed to be empty)
        assert len(self.header) in (0, self.num_columns)
        assert len(self.footer) in (0, self.num_columns)
        for row in self.contents:
            assert len(row) in (0, self.num_columns)

        self._invalidate()

    def _get_column_widths(self, overall_width: int) -> list[int]:
        """Given the overall width, calculate the width allowed for column."""

        max_col_widths = [
            max([len(self.contents[i][j]) for i in range(self.num_rows)]) for j in range(self.num_columns)
        ]
        if len(self.header) > 0:
            for j in range(self.num_columns):
                max_col_widths[j] = max(max_col_widths[j], len(self.header[j]))
        if len(self.footer) > 0:
            for j in range(self.num_columns):
                max_col_widths[j] = max(max_col_widths[j], len(self.footer[j]))

        # If everything fits without wrapping, great!
        non_column_characters = len(self.ROW_COL_DIVIDER) * (self.num_columns - 1)
        if non_column_characters + sum(max_col_widths) <= overall_width:
            return max_col_widths

        # If we have to wrap columns, do so proportionally with the 'weight'
        # (total text length) of each column
        col_weights = [sum([len(self.contents[i][j]) for i in range(self.num_rows)]) for j in range(self.num_columns)]
        if len(self.header) > 0:
            for j in range(self.num_columns):
                col_weights[j] += len(self.header[j])
        if len(self.footer) > 0:
            for j in range(self.num_columns):
                col_weights[j] += len(self.footer[j])
        total_weight = sum(col_weights)

        # Calculate the initial column widths based on the column 'weights'
        scaling_factor = 1.0 * (overall_width - non_column_characters) / total_weight
        col_widths = [min(width, round(scaling_factor * weight)) for width, weight in zip(max_col_widths, col_weights)]

        # Because it is possible that some columns did not need their weight's
        # full allocation (if they were narrower), dole out additional width to
        # the columns whose 'weight x (max_col_width - current_col_width)' is
        # largest' until we either run out of width or columns which want it
        while non_column_characters + sum(col_widths) < overall_width:
            biggest_discrepancy = max(
                range(len(col_widths)), key=lambda i: 1.0 * col_weights[i] * (max_col_widths[i] - col_widths[i])
            )
            if col_widths[biggest_discrepancy] < max_col_widths[biggest_discrepancy]:
                col_widths[biggest_discrepancy] += 1
            else:
                break

        assert non_column_characters + sum(col_widths) <= overall_width
        return col_widths

    @staticmethod
    def _align_to_char(align: urwid.Align) -> str:
        if align == urwid.CENTER:
            return "^"
        if align == urwid.RIGHT:
            return ">"
        assert align == urwid.LEFT
        return "<"

    def _render_cell(self, text: str, width: int, align: urwid.Align = urwid.LEFT) -> list[str]:
        """
        Render a single cell of text into a list of string rows within that
        cell (taking however many rows are required to display the entire
        text).
        """
        use_indentation = width > len(self.INDENTATION) * 2
        indentation_width = len(self.INDENTATION) if use_indentation else 0
        align_char = self._align_to_char(align)

        rows = []
        words = text.split(" ")
        this_line = ""
        while words:
            first_in_line = (not rows and not this_line) or (
                (this_line == self.INDENTATION) if use_indentation else len(this_line) == 0
            )
            prefix = "" if first_in_line else " "
            line_length = len(this_line) + len(prefix)
            remaining_width = width - line_length
            next_word = words[0]
            if len(next_word) <= remaining_width:
                # The next word can still fit in the current line
                this_line += f"{prefix}{next_word}"
                words.pop(0)
            elif len(next_word) > width - indentation_width and (first_in_line or line_length < (width / 2)):
                # break the word up because it can't fit in one line anyway
                partial_next_word = next_word[: remaining_width - len(self.LINE_CONTINUATION)]
                words[0] = next_word[remaining_width - len(self.LINE_CONTINUATION) :]
                this_line += f"{prefix}{partial_next_word}{self.LINE_CONTINUATION}"
                rows.append(f"{this_line:{align_char}{width}}")
                this_line = self.INDENTATION if use_indentation else ""
            else:
                # do not place the next word on the current line - instead
                # write the current line out and begin a new line
                rows.append(f"{this_line:{align_char}{width}}")
                this_line = self.INDENTATION if use_indentation else ""

        # Finish any partial rows
        if len(this_line) > 0 and (not use_indentation or this_line != self.INDENTATION):
            rows.append(f"{this_line:{align_char}{width}}")

        return rows

    def _render_row(self, columns: Sequence[str], widths: list[int]) -> list[str]:
        """
        Render a row of cells into a list of strings - one string per row of
        the output.
        """
        assert len(columns) == len(widths)
        rendered_cells = [self._render_cell(col, width) for col, width in zip(columns, widths)]
        max_rows = max(len(cell) for cell in rendered_cells)

        for idx, (cell, width) in enumerate(zip(rendered_cells, widths)):
            if len(cell) < max_rows:
                rendered_cells[idx] = cell + [" " * width] * (max_rows - len(cell))

        return [self.COL_DIVIDER.join(r) for r in zip(*rendered_cells)]

    def _render_padded_row(self, row: str, attr: str, width: int) -> tuple[bytes, list[tuple[str, int]]]:
        if self.align == urwid.CENTER:
            left_pad = (width - len(row)) // 2
            right_pad = width - len(row) - left_pad
        elif self.align == urwid.RIGHT:
            left_pad = 0
            right_pad = width - len(row)
        else:
            assert self.align == urwid.RIGHT
            left_pad = width - len(row)
            right_pad = 0

        left_pad = f"{'':<{left_pad}}".encode()
        row_bytes = row.encode()
        right_pad = f"{'':<{right_pad}}".encode()

        attrs = []
        if left_pad:
            attrs.append(("body", len(left_pad)))
        attrs.append((attr, len(row_bytes)))
        if right_pad:
            attrs.append(("body", len(right_pad)))

        return left_pad + row_bytes + right_pad, attrs

    def _render_divider(self, widths: list[int]) -> list[str]:
        """
        Render a horizontal divider between rows in the table.
        """
        return [self.ROW_COL_DIVIDER.join([self.ROW_DIVIDER * width for width in widths])]

    def _render_table(self, width: int) -> tuple[list[bytes], list[list[tuple[str, int]]]]:
        """
        Render the table to a list of strings - one string per textual row of
        the table (each cell in the table may take up more than one row of text).
        """
        col_widths = self._get_column_widths(width)
        rows = []
        attrs = []

        if self.title:
            for row in self._render_cell(self.title, width, self.align):
                rows.append(row.encode())
                attrs.append([("focused_event_row", len(rows[-1]))])
            rows.append((" " * width).encode())
            attrs.append([("body", len(rows[-1]))])

        def pad_add_row(row: str, attr: str):
            row_text, row_attrs = self._render_padded_row(row, attr, width)
            rows.append(row_text)
            attrs.append(row_attrs)

        row_idx = 0
        if len(self.header) > 0:
            attr = f"{even_odd(row_idx)}_event_row"
            for r in self._render_row(self.header, col_widths):
                pad_add_row(r, attr)
            row_idx += 1
            for r in self._render_divider(col_widths):
                pad_add_row(r, "body")
        for row in self.contents:
            attr = f"{even_odd(row_idx)}_event_row"
            for r in self._render_row(row, col_widths):
                pad_add_row(r, attr)
            row_idx += 1
        if len(self.footer) > 0:
            attr = f"{even_odd(row_idx)}_event_row"
            for r in self._render_divider(col_widths):
                pad_add_row(r, "body")
            for r in self._render_row(self.footer, col_widths):
                pad_add_row(r, attr)
            row_idx += 1

        return rows, attrs

    def rows(self, size: tuple[int], focus: bool = False) -> int:
        (maxcol,) = size
        lines, a = self._render_table(maxcol)
        return len(lines)

    def render(
        self,
        size: tuple[()] | tuple[int] | tuple[int, int],
        focus: bool = False,
    ) -> urwid.canvas.Canvas:
        (maxcol,) = size
        lines, attrs = self._render_table(maxcol)

        return urwid.canvas.TextCanvas(lines, attrs)
