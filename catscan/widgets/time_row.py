# Copyright (c) 2024 Ampere Computing. All rights reserved.
# SPDX-License-Identifier: BSD-3-Clause

from typing import NamedTuple

import urwid

from catscan.data import EventStreamData
from catscan.state import CatscanState


class TimeRowLabel(NamedTuple):
    start_column: int
    label: str


class TimeRow(urwid.widget.Widget):
    """
    Display a bar with 'ticks' for the flow of time, along with a bar
    representing where the current view falls within the available time of the
    event stream as a whole.
    """

    _sizing = frozenset(["flow"])
    _selectable = False

    def __init__(self, stream_data: EventStreamData, state: CatscanState, in_cycles: bool = True) -> None:
        self.stream_data = stream_data
        self.state = state
        self.in_cycles = in_cycles
        super().__init__()

    def update_stream_data(self, stream_data: EventStreamData) -> None:
        self.stream_data = stream_data
        self._invalidate()

    def update_state(self, new_state: CatscanState) -> None:
        if new_state == self.state:
            return

        self.state = new_state
        self._invalidate()

    def rows(self, size: tuple[int], focus: bool = False) -> int:
        return 2

    def _ps_to_column(self, ps: int) -> int:
        """
        Convert a time in picoseconds to a character index in the time row.
        """
        return (ps - self.state.start_ps) // (self.state.ps_per_cycle * self.state.cycles_per_char)

    def _get_visible_marks(self, start_ps: int, end_ps: int) -> list[TimeRowLabel]:
        visible_marks = []
        for mark, event in sorted(self.state.marked_events.items(), key=lambda p: p[1].time):
            if event.time < start_ps or event.time >= end_ps:
                continue

            start_column = self._ps_to_column(event.time)
            label = f"╭ '{mark}"

            # combine with previous mark if overlapping
            combine_with_previous = False
            if visible_marks:
                last_mark = visible_marks[-1]
                combine_with_previous = last_mark.start_column + len(last_mark.label) + 1 >= start_column
                if combine_with_previous:
                    visible_marks[-1] = TimeRowLabel(last_mark.start_column, f"{last_mark.label},{mark}")

            if not combine_with_previous:
                visible_marks.append(TimeRowLabel(start_column, label))
        return visible_marks

    def _get_visible_times(self, start_ps: int, end_ps: int, columns: int) -> list[TimeRowLabel]:
        time_len = len(f"{end_ps:,}") + 3
        approx_max_labels = round((columns - self.state.column_header_width) / time_len)

        time_difference = end_ps - start_ps
        ps_granularity = max(self.state.ps_per_cycle, round(self.state.ps_per_cycle * self.state.cycles_per_char))
        while time_difference / ps_granularity > approx_max_labels:
            ps_granularity *= 2

        start_label_time = (start_ps // ps_granularity) * ps_granularity
        potential_label_times = list(range(start_label_time, end_ps + round(ps_granularity), round(ps_granularity)))

        visible_times = []
        for time in potential_label_times:
            label_time = round(time / self.state.ps_per_cycle) if self.in_cycles else time
            label = f"╭ {label_time:,}"
            start_column = self._ps_to_column(time)
            if start_column >= 0 and start_column < columns:
                visible_times.append(TimeRowLabel(start_column, label))

        return visible_times

    def get_time_row(self, columns: int) -> bytes:
        label = "time (cycles)" if self.in_cycles else "time (in ps)"
        header = f"{label:>{self.state.column_header_width - 1}}│"[-self.state.column_header_width :]
        time_row = ""

        end_ps = self.state.start_ps + round(
            self.state.ps_per_cycle * self.state.cycles_per_char * (columns - self.state.column_header_width)
        )
        time_labels = self._get_visible_times(self.state.start_ps, end_ps, columns)
        mark_labels = self._get_visible_marks(self.state.start_ps, end_ps)

        while len(time_row) < columns - self.state.column_header_width - 1:
            col = len(time_row)
            if mark_labels and mark_labels[0].start_column == col:
                mark_label = mark_labels.pop(0)
                time_row += mark_label.label
            elif time_labels and time_labels[0].start_column < col:
                time_label = time_labels.pop(0)
            elif time_labels and time_labels[0].start_column == col:
                time_label = time_labels.pop(0)
                # Omit this time label if it'll overlap with the next mark label
                if not mark_labels or time_label.start_column + len(time_label.label) + 2 < mark_labels[0].start_column:
                    time_row += time_label.label
            else:
                time_row += " "

        time_row = time_row[: columns - self.state.column_header_width - 1]

        return (header + time_row + "│").encode()

    def get_divider(self, columns: int) -> bytes:
        available_bar_columns = columns - self.state.column_header_width - 1
        # This is the default divider row
        divider_row = "─" * (self.state.column_header_width - 1) + "┼" + "─" * available_bar_columns
        # Now, see if we can add a 'progress bar'
        end_ps = self.state.start_ps + round(
            self.state.ps_per_cycle * self.state.cycles_per_char * (columns - self.state.column_header_width)
        )
        midpoint_ps = round((self.state.start_ps + end_ps) / 2)
        total_ps = self.stream_data.last_time - self.stream_data.first_time
        pct_complete = (midpoint_ps - self.stream_data.first_time) / total_ps if total_ps > 0 else 0
        pct_shown = (end_ps - self.state.start_ps) / total_ps if total_ps > 0 else 0
        bar_centerpoint_col = round(pct_complete * available_bar_columns + self.state.column_header_width)
        bar_half_width_columns = max(0, round(pct_shown * available_bar_columns / 2) - 1)
        bar_start_col = bar_centerpoint_col - bar_half_width_columns
        bar_stop_col = bar_centerpoint_col + bar_half_width_columns + 1
        if bar_start_col < self.state.column_header_width:
            bar_start_col = self.state.column_header_width + 1
            divider_row = (
                divider_row[: self.state.column_header_width] + "<" + divider_row[self.state.column_header_width + 1 :]
            )
        if bar_stop_col > columns - 1:
            bar_stop_col = columns - 1
            divider_row = divider_row[:-1] + ">"
        for c in range(bar_start_col, bar_stop_col):
            divider_row = divider_row[:c] + "■" + divider_row[c + 1 :]
        divider_row += "┤"
        return divider_row.encode()

    def render(
        self,
        size: tuple[()] | tuple[int] | tuple[int, int],
        focus: bool = False,
    ) -> urwid.canvas.Canvas:
        maxcol = size[0]
        return urwid.canvas.TextCanvas([self.get_time_row(maxcol), self.get_divider(maxcol)])
