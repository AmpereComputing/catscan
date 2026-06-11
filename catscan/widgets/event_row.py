# Copyright (c) 2024 Ampere Computing. All rights reserved.
# SPDX-License-Identifier: BSD-3-Clause

import math
from collections.abc import Callable
from enum import Enum
from fractions import Fraction
from functools import lru_cache
from typing import Any

import urwid

from catscan.data import NUM_EVENT_COLORS, Event, EventData, TransactionEventData
from catscan.search import Searcher
from catscan.state import CatscanState, HashableFrozenDict, Selection
from catscan.user_input import ACTIONS, action_keypresses, action_mouseevents
from catscan.util import even_odd_focused, str_fit_width


# TODO should this grow?
# TODO should we only call the cached version for zoomed-out operation?
@lru_cache(maxsize=16384)
def summarize_event_row_aligned(
    ed: EventData,
    ps_per_cycle: int,
    start_ps: int,
    desired_datapoints: int,
    cycles_per_datapoint: int,
    distinct_levels: int,
    min_level: int | None,
    max_value: int,
    highlighted_transactions: HashableFrozenDict,
    searcher: Searcher | None,
) -> tuple[list[int], list[int]]:
    """
    Summarize an event row's data given a particular zoom level and
    searching/highlighting state. This is an "aligned" version of
    summarize_event_row, below. The purpose of the separation is to allow for
    efficient caching of the underlying data/computation, even if the
    underlying user-interface displays the same data at different offsets.

    This function deals heavily with summarizing data when we are unable to
    represent it at its full 'resolution' due to display limitations. To work
    around this, we use a 'dithering' technique to carry forward the cumulative
    error between the desired value and the representable value (both for
    frequency of events and color values) such that the display most closely
    represents the overall picture, even though it is "lying" to us for
    individual characters.
    """
    highlighting_transactions = bool(highlighted_transactions)
    highlighting_search = searcher is not None
    highlighting_search_row = highlighting_search and searcher.search_event_row(ed)
    highlighting = highlighting_transactions or highlighting_search

    scaling_factor = (distinct_levels - 1) / max_value
    reverse_scaling_factor = max_value / (distinct_levels - 1)
    # accumulated_error tracks the accumulated error for the event count
    accumulated_error = 0.0
    # color_error and max_error_color track the accumulated error for event
    # colors
    color_error = [0] * NUM_EVENT_COLORS
    max_error_color = 0
    # seen_highlights tracks the colors that have been highlighted
    seen_highlights = set()
    # approximate "min-value" level value
    min_level_value = 0.25
    # error falloff level value for empty columns (will trend towards 0)
    falloff_ratio = 0.2

    step = cycles_per_datapoint * ps_per_cycle
    current_ps = start_ps + step
    events_occurred = 0
    output = []
    colors = []

    def accumulate_events():
        nonlocal accumulated_error, color_error, max_error_color, events_occurred, seen_highlights
        scaled_events_occurred = scaling_factor * events_occurred
        error_corrected_count = (scaled_events_occurred + accumulated_error) / cycles_per_datapoint
        level_value = char_level_idx = max(0, min(round(error_corrected_count), distinct_levels - 1))
        if highlighting and level_value == 0 and (len(seen_highlights) > 0 or color_error[max_error_color] > 0):
            # Force us to output the lowest level character this cycle because
            # there was a highlight that needs to get displayed. We'll fixup
            # the other error later
            char_level_idx = min_level or 1
        elif min_level is not None and level_value == 0 and scaled_events_occurred > 0:
            char_level_idx = min_level

        if char_level_idx == min_level:
            level_value = min_level_value

        error = scaled_events_occurred - level_value * cycles_per_datapoint
        prev_accumulated_error = accumulated_error
        accumulated_error += error

        if char_level_idx == min_level and error < 0:
            accumulated_error = max(0, accumulated_error) if prev_accumulated_error > 0 else prev_accumulated_error
        elif char_level_idx == 0:
            accumulated_error *= falloff_ratio

        output.append(char_level_idx)

        # If we are highlighting, we wait until the end of the cycle to account
        # for the colors, because we want to highlight anything that is
        # highlighted equally, regardless of its frequency (the assumption is
        # that highlighting should be relatively infrequent, and that we don't
        # want to obscure anything that is highlighted)
        if highlighting:
            events_per_unique_highlight = len(seen_highlights) / events_occurred if events_occurred != 0 else 0
            for color_idx in seen_highlights:
                color_error[color_idx] += events_per_unique_highlight
                if color_error[color_idx] > color_error[max_error_color]:
                    max_error_color = color_idx
            seen_highlights = set()

        if color_error[max_error_color] > 0:
            colors.append(max_error_color)

            if highlighting:
                color_error[max_error_color] = 0
            else:
                color_error[max_error_color] = max(
                    0, color_error[max_error_color] - (level_value * reverse_scaling_factor * cycles_per_datapoint)
                )
                if char_level_idx == 0:
                    for color_idx in range(NUM_EVENT_COLORS):
                        color_error[color_idx] *= falloff_ratio

            max_error_color = max(enumerate(color_error), key=lambda e: e[1])[0]
        else:
            colors.append(None)

        events_occurred = 0

    for event in ed[start_ps : start_ps + step * desired_datapoints]:
        while current_ps <= event.time:
            accumulate_events()
            current_ps += step

        if highlighting:
            if highlighting_search_row and searcher.match(event):
                seen_highlights.add(7)
            if highlighting_transactions and "txid" in event.data:
                txid = event.data["txid"]
                if txid in highlighted_transactions:
                    seen_highlights.add(highlighted_transactions[txid])
        else:
            color_idx = event.color_idx
            color_error[color_idx] += 1
            if color_error[color_idx] > color_error[max_error_color]:
                max_error_color = color_idx
        events_occurred += 1
    if events_occurred > 0:
        accumulate_events()

    output += [0] * (desired_datapoints - len(output))
    colors += [0] * (desired_datapoints - len(colors))
    return output, colors


SUMMARY_BLOCK_SIZE = 256


def summarize_event_row(
    ed: EventData,
    ps_per_cycle: int,
    start_ps: int,
    desired_datapoints: int,
    cycles_per_datapoint: int,
    distinct_levels: int,
    min_level: int | None,
    max_value: int,
    highlighted_transactions: HashableFrozenDict,
    searcher: Searcher | None,
) -> tuple[list[int], list[int]]:
    """
    Summarize an event row's data given a particular zoom level and
    searching/highlighting state. This handles stitching different pieces of
    "aligned" data together from summarize_event_row_aligned.
    """
    ps_per_datapoint = cycles_per_datapoint * ps_per_cycle
    assert (start_ps % ps_per_datapoint) == 0, (
        "start_ps is not aligned to a multiple of ps_per_cycle * cycles_per_datapoint"
    )

    start_datapoint_idx = start_ps // ps_per_datapoint
    output = []
    colors = []
    while len(output) < desired_datapoints:
        # calculate the starting time in picoseconds of the aligned region
        aligned_datapoint_idx = (start_datapoint_idx // SUMMARY_BLOCK_SIZE) * SUMMARY_BLOCK_SIZE
        aligned_start_ps = aligned_datapoint_idx * ps_per_datapoint
        aligned_output, aligned_colors = summarize_event_row_aligned(
            ed,
            ps_per_cycle,
            aligned_start_ps,
            SUMMARY_BLOCK_SIZE,
            cycles_per_datapoint,
            distinct_levels,
            min_level,
            max_value,
            highlighted_transactions,
            searcher,
        )
        skip_datapoint_indices = start_datapoint_idx - aligned_datapoint_idx
        assert skip_datapoint_indices < SUMMARY_BLOCK_SIZE
        output += aligned_output[
            skip_datapoint_indices : min(skip_datapoint_indices + desired_datapoints - len(output), SUMMARY_BLOCK_SIZE)
        ]
        colors += aligned_colors[
            skip_datapoint_indices : min(skip_datapoint_indices + desired_datapoints - len(colors), SUMMARY_BLOCK_SIZE)
        ]
        start_datapoint_idx += SUMMARY_BLOCK_SIZE - skip_datapoint_indices
    return output, colors


@lru_cache(maxsize=16384)
def cycle_events_aligned(ed: EventData, ps_per_cycle: int, start_ps: int, desired_datapoints: int) -> list[list[Event]]:
    """
    Aligned version of cycle_events, below. The purpose of the separation
    between cycle_events_aligned and cycle_events is to allow for efficient
    caching of the underlying data/computation, even if the underlying
    user-interface displays the same data at different offsets.
    """
    current_ps = start_ps
    events_occurred = []
    output = []

    for event in ed[start_ps : start_ps + ps_per_cycle * desired_datapoints]:
        while current_ps < event.time:
            assert len(events_occurred) <= ed.max_events_per_time(), (
                f"Attempting to output more events per time ({len(events_occurred)}) than the underlying EventData object reports ({ed.max_events_per_time()}). This likely means the specified `--period` argument (set to {ps_per_cycle} ps) is smaller than the actual cycle length of the simulation used to generate the event stream. Debugging data: current_ps: {current_ps}, events_occurred: {events_occurred}"
            )
            output.append(events_occurred)
            events_occurred = []
            current_ps += ps_per_cycle
        events_occurred.append(event)
    if len(events_occurred) > 0:
        output.append(events_occurred)

    output += [[] for i in range(desired_datapoints - len(output))]
    return output


def cycle_events(ed: EventData, ps_per_cycle: int, start_ps: int, desired_datapoints: int) -> list[list[Event]]:
    """
    Return a list of lists of events in this EventData object. Each element in
    the top-level list represents one cycle, and contains a list of all Events
    in this EventData object within that cycle.
    """
    assert (start_ps % ps_per_cycle) == 0, (
        f"start_ps ({start_ps}) is not aligned to a multiple of ps_per_cycle ({ps_per_cycle})"
    )

    start_datapoint_idx = start_ps // ps_per_cycle
    output = []
    while len(output) < desired_datapoints:
        # calculate the starting time in picoseconds of the aligned region
        aligned_datapoint_idx = (start_datapoint_idx // SUMMARY_BLOCK_SIZE) * SUMMARY_BLOCK_SIZE
        aligned_start_ps = aligned_datapoint_idx * ps_per_cycle
        aligned_output = cycle_events_aligned(ed, ps_per_cycle, aligned_start_ps, SUMMARY_BLOCK_SIZE)
        skip_datapoint_indices = start_datapoint_idx - aligned_datapoint_idx
        assert skip_datapoint_indices < SUMMARY_BLOCK_SIZE
        output += aligned_output[
            skip_datapoint_indices : min(skip_datapoint_indices + desired_datapoints - len(output), SUMMARY_BLOCK_SIZE)
        ]
        start_datapoint_idx += SUMMARY_BLOCK_SIZE - skip_datapoint_indices
    return output


# Describes the type of row being referred to
RowType = Enum("RowType", ["RESOURCE_BASE", "EVENT", "GROUP"])


class EventRowBase(urwid.widget.Widget):
    _sizing = frozenset(["flow"])
    _selectable = True

    def row_id(self) -> tuple[RowType, str]:
        return RowType.RESOURCE_BASE, "empty"

    def update_state(self, new_state: CatscanState) -> None:
        pass

    def rows(self, size: tuple[int], focus: bool = False) -> int:
        return 1

    def render(
        self,
        size: tuple[()] | tuple[int] | tuple[int, int],
        focus: bool = False,
    ) -> urwid.canvas.Canvas:
        return urwid.canvas.TextCanvas([(" " * size[0]).encode()])


class GroupRow(EventRowBase):
    def __init__(self, name: str, state: CatscanState, row_index: int):
        self.name = name
        self.state = state
        self.row_index = row_index
        super().__init__()

    def selectable(self) -> bool:
        return False

    def row_id(self) -> tuple[RowType, str]:
        return RowType.GROUP, self.name

    def update_state(self, new_state: CatscanState) -> None:
        self.state = new_state

    def rows(self, size: tuple[int], focus: bool = False) -> int:
        return 1

    @staticmethod
    def max_column_header_width(row_name: str) -> int:
        return len(row_name) + 5

    def render(
        self,
        size: tuple[()] | tuple[int] | tuple[int, int],
        focus: bool = False,
    ) -> urwid.canvas.Canvas:
        maxcol = size[0]
        surrounded_name = f"[ {self.name} ]"
        header = f"{surrounded_name:═^{self.state.column_header_width - 1}}╪"[-self.state.column_header_width :]
        line = f"{header:═<{maxcol}}".encode()
        has_focus = focus and self.state.has_focus
        attr = f"{even_odd_focused(self.row_index, has_focus)}_group_row"
        return urwid.canvas.TextCanvas([line], [[(attr, len(line))]])


class EventRow(EventRowBase):
    """
    Display a single row of events. If the view is zoomed out far enough or
    'condensed', each character may represent multiple cycles or multiple
    events.
    """

    SHADING = " ░▒▓█"
    BARS = " ▂▃▄▅▆▇█"
    MIN_BAR = "."

    EMPTY_CHAR = " "
    NON_EMPTY_CHAR = "·"
    FULL_CHAR = "▇"

    MORE_LEFT_CHAR = "◀"
    MORE_RIGHT_CHAR = "▶"
    MORE_THRESHOLD = 8

    # TODO override focus() to return the child widget with focus, if applicable

    def __init__(
        self,
        ed: EventData,
        state: CatscanState,
        row_index: int,
        on_make_selection: Callable,
        on_extend_selection: Callable,
        active_background: str | None = None,
        expanded_allowed: bool = True,
    ) -> None:
        self.ed = ed
        self.state = state
        self.row_index = row_index
        self.on_make_selection = on_make_selection
        self.on_extend_selection = on_extend_selection
        self.active_background = active_background
        self.expanded_allowed = expanded_allowed
        super().__init__()

    @property
    def expanded(self) -> bool:
        return self.state.expand_rows and self.expanded_allowed

    def row_id(self) -> tuple[RowType, str]:
        return RowType.EVENT, self.ed.key()

    def update_state(self, new_state: CatscanState) -> None:
        if new_state != self.state:
            self.state = new_state
            self._invalidate()

    @staticmethod
    def max_column_header_width(row_name: str) -> int:
        return len(row_name) + 3

    def get_column_header(self) -> str:
        return f"- {self.ed.short_name:<{self.state.column_header_width - 3}}│"[-self.state.column_header_width :]

    def ps_range_to_indices(self, start_ps: int, end_ps: int, cycles_per_index: int | Fraction) -> tuple[int, int]:
        """Helper to return the starting/ending indices of a picosecond range."""
        start_offset_ps = self.state.start_ps - (self.state.start_ps % self.state.ps_per_cycle)
        indices_per_ps = 1.0 / (self.state.ps_per_cycle * cycles_per_index)
        start_index = math.floor((start_ps - start_offset_ps) * indices_per_ps)
        end_index = math.floor((end_ps - 1 - start_offset_ps) * indices_per_ps)
        return start_index, end_index

    def index_to_ps_range(self, index: int, cycles_per_index: int | Fraction) -> tuple[int, int]:
        """
        Helper to return the picosecond range covered by a single 'index' (most
        typically either a cycle or column).
        """
        ps_per_index = self.state.ps_per_cycle * cycles_per_index
        difference_ps = self.state.start_ps % self.state.ps_per_cycle
        difference_indices = math.floor(difference_ps / ps_per_index)

        start_ps = round(self.state.start_ps - difference_ps + (index + difference_indices) * ps_per_index)
        end_ps = round(start_ps + ps_per_index)
        return start_ps, end_ps

    def selection_start_end(self, cycles_per_index: int | Fraction) -> tuple[int, int]:
        """
        Helper to return the starting/ending indices of the current selection
        as indices into the current list of events or event-summary data.
        """
        if not self.state.selection:
            return (-1, -1)
        return self.ps_range_to_indices(self.state.selection.start_ps, self.state.selection.end_ps, cycles_per_index)

    def first_last(self, cycles_per_index: int | Fraction) -> tuple[int, int]:
        return self.ps_range_to_indices(
            self.ed.first_time,
            self.ed.last_time + self.state.ps_per_cycle,
            cycles_per_index,
        )

    def start_end(self, cycles_per_index: int | Fraction) -> tuple[int, int]:
        return self.ps_range_to_indices(
            self.ed.start_time,
            self.ed.end_time + self.state.ps_per_cycle,
            cycles_per_index,
        )

    def _more_left(self, data: tuple[list[int], list[int]], index: int, first: int, start: int, end: int) -> bool:
        if index != 0 or first >= index:
            return False
        if self.active_background:
            return not (start <= index <= end)

        return all((item if isinstance(item, int) else len(item)) == 0 for item in data[: self.MORE_THRESHOLD])

    def _more_right(self, data: tuple[list[int], list[int]], index: int, last: int, start: int, end: int) -> bool:
        if index != len(data) - 1 or last <= index:
            return False
        if self.active_background:
            return not (start <= index <= end)

        return all((item if isinstance(item, int) else len(item)) == 0 for item in data[-self.MORE_THRESHOLD :])

    def get_condensed_line(
        self, width: int, default_attr: str, focus: bool
    ) -> tuple[list[list[tuple[str, int]]], list[bytes]]:
        header = self.get_column_header()
        assert width > len(header)
        characterset = EventRow.BARS + EventRow.MIN_BAR

        # Get the number of picoseconds and then characters to chop off the
        # beginning of the first partial cycle
        difference_ps = self.state.start_ps % self.state.ps_per_cycle
        difference_chars = math.floor(difference_ps / self.state.ps_per_char)
        start_ps = self.state.start_ps - difference_ps

        if self.state.cycles_per_char.denominator > 1:
            desired_datapoints = math.ceil(
                (width - self.state.column_header_width + difference_chars) * self.state.cycles_per_char
            )
            cycles_per_datapoint = 1
        else:
            desired_datapoints = width - self.state.column_header_width
            cycles_per_datapoint = self.state.cycles_per_char.numerator

        if self.ed.max_events_per_time() == 0:
            self.data = [0] * (width - self.state.column_header_width)
            colors = self.data
        else:
            self.data, colors = summarize_event_row(
                self.ed,
                ps_per_cycle=self.state.ps_per_cycle,
                start_ps=start_ps,
                desired_datapoints=desired_datapoints,
                cycles_per_datapoint=cycles_per_datapoint,
                distinct_levels=len(EventRow.BARS),
                min_level=len(EventRow.BARS),
                max_value=self.ed.max_events_per_time(),
                highlighted_transactions=self.state.highlighted_transactions,
                searcher=self.state.searcher,
            )

        byte_string = header.encode()
        attrs = [(default_attr, len(byte_string))]

        # Determine which range of 'datapoints' (refers to at least one column
        # of the screen, maybe more) should be highlighted if a selection has
        # been made
        selection_start, selection_end = self.selection_start_end(cycles_per_datapoint)
        start, end = self.start_end(cycles_per_datapoint)
        first, last = self.first_last(cycles_per_datapoint)

        remaining_width = width - self.state.column_header_width
        for i, char_idx in enumerate(self.data):
            has_focus = (selection_start <= i <= selection_end) != focus and self.state.has_focus
            empty_attr = f"{even_odd_focused(self.row_index, has_focus)}_event_row"

            cols_this_item = min(remaining_width, self.state.cycles_per_char.denominator)
            if i == 0 and difference_chars != 0:
                cols_this_item -= difference_chars
            assert cols_this_item > 0

            more_left = self._more_left(self.data, i, first, start, end)
            more_right = self._more_right(self.data, i, last, start, end)
            if more_left:
                character_bytes = (self.MORE_LEFT_CHAR + self.EMPTY_CHAR * (cols_this_item - 1)).encode()
            elif more_right:
                character_bytes = (self.EMPTY_CHAR * (cols_this_item - 1) + self.MORE_RIGHT_CHAR).encode()
            else:
                character_bytes = (characterset[char_idx] * cols_this_item).encode()
            byte_string += character_bytes

            if not more_right and not more_left and char_idx > 0:
                color_idx = colors[i]
                attr = f"event_color_reversed_{color_idx}_{even_odd_focused(self.row_index, has_focus)}"
            else:
                attr = empty_attr

            if self.active_background is not None and (start <= i <= end):
                attr += self.active_background

            attrs.append((attr, len(character_bytes)))
            remaining_width -= cols_this_item

        return [attrs], [byte_string]

    def render_event(self, event: Event, width: int) -> tuple[int, str]:
        color_idx = event.color_idx
        return color_idx, str_fit_width(event.abbrev, width, pad="^")

    def get_expanded_lines_show_abbrev(
        self, rows: int, width: int, default_attr: str, focus: bool
    ) -> tuple[list[list[tuple[str, int]]], list[bytes]]:
        assert self.state.cycles_per_char.numerator == 1, "Numerator must be 1 to call get_expanded_lines_show_abbrev"
        header = self.get_column_header()
        assert width > len(header)
        header_below = " " * (self.state.column_header_width - 1) + "│"
        byte_strings = [header.encode()] + [header_below.encode() for r in range(rows - 1)]
        attrs = [[(default_attr, len(header))] for header in byte_strings]

        # Get the number of picoseconds and then characters to chop off the
        # beginning of the first partial cycle
        difference_ps = self.state.start_ps % self.state.ps_per_cycle
        difference_chars = math.floor(difference_ps / self.state.ps_per_char)
        desired_cycles = math.ceil(
            (width - self.state.column_header_width + difference_chars) * self.state.cycles_per_char
        )
        start_ps = self.state.start_ps - difference_ps

        if self.ed.max_events_per_time() == 0:
            self.data = [[] for d in range(desired_cycles)]
        else:
            self.data = cycle_events(
                self.ed, ps_per_cycle=self.state.ps_per_cycle, start_ps=start_ps, desired_datapoints=desired_cycles
            )
            assert len(self.data) == desired_cycles

        highlighting_transactions = len(self.state.highlighted_transactions) != 0
        highlighting_search = self.state.searcher is not None
        highlighting_search_row = highlighting_search and self.state.searcher.search_event_row(self.ed)
        highlighting = highlighting_transactions or highlighting_search

        # Determine which 'columns' (refers to at least one column of the
        # screen, maybe more) should be highlighted if a selection has been
        # made.
        selection_start, selection_end = self.selection_start_end(1)
        start, end = self.start_end(1)
        first, last = self.first_last(1)

        cols_per_cycle = self.state.cycles_per_char.denominator
        remaining_width = width - self.state.column_header_width
        for i, events in enumerate(self.data):
            cols_this_cycle = cols_per_cycle
            if i == 0:
                cols_this_cycle -= difference_chars
            cols_this_cycle = min(cols_this_cycle, remaining_width)
            assert cols_this_cycle > 0

            has_focus = (selection_start <= i <= selection_end) != focus and self.state.has_focus
            more_left = self._more_left(self.data, i, first, start, end)
            more_right = self._more_right(self.data, i, last, start, end)
            display_events = events[:-1] if (more_left or more_right) and len(events) == rows else events

            for l, event in enumerate(display_events):
                color_idx, text = self.render_event(event, cols_this_cycle)

                if highlighting_search_row and self.state.searcher.match(event):
                    color_idx = 7
                elif (
                    highlighting_transactions
                    and "txid" in event.data
                    and event.data["txid"] in self.state.highlighted_transactions
                ):
                    color_idx = self.state.highlighted_transactions[event.data["txid"]]
                elif highlighting:
                    color_idx = None
                byte_string = text.encode()
                byte_strings[l] += byte_string

                if self.state.selection.is_event() and self.state.selection.event.id == event.id:
                    attr = f"event_color_reversed_{color_idx}_{even_odd_focused(self.row_index, has_focus)}"
                    if self.active_background is not None and (start <= i <= end):
                        attr += self.active_background
                else:
                    attr = f"event_color_{color_idx}"

                attrs[l].append((attr, len(byte_string)))

            empty_attr = f"{even_odd_focused(self.row_index, has_focus)}_event_row"

            for l in range(len(display_events), rows):
                if more_left:
                    byte_string = (self.MORE_LEFT_CHAR + self.EMPTY_CHAR * (cols_this_cycle - 1)).encode()
                elif more_right:
                    byte_string = (self.EMPTY_CHAR * (cols_this_cycle - 1) + self.MORE_RIGHT_CHAR).encode()
                else:
                    byte_string = (self.EMPTY_CHAR * cols_this_cycle).encode()

                byte_strings[l] += byte_string
                attr = empty_attr
                if self.active_background is not None and (start <= i <= end):
                    attr += self.active_background
                attrs[l].append((attr, len(byte_string)))

            remaining_width -= cols_this_cycle

        return attrs, byte_strings

    def get_expanded_lines(
        self, rows: int, width: int, default_attr: str, focus: bool
    ) -> tuple[list[list[tuple[str, int]]], list[bytes]]:
        header = self.get_column_header()
        assert width > len(header)
        header_below = " " * (self.state.column_header_width - 1) + "│"

        assert self.state.cycles_per_char.denominator == 1
        difference_ps = self.state.start_ps % self.state.ps_per_cycle
        start_ps = self.state.start_ps - difference_ps

        if self.ed.max_events_per_time() == 0:
            self.data = [0] * (width - self.state.column_header_width)
            colors = self.data
        else:
            self.data, colors = summarize_event_row(
                self.ed,
                ps_per_cycle=self.state.ps_per_cycle,
                start_ps=start_ps,
                desired_datapoints=width - self.state.column_header_width,
                cycles_per_datapoint=self.state.cycles_per_char.numerator,
                distinct_levels=rows + 1,
                min_level=-1,
                max_value=rows,
                highlighted_transactions=self.state.highlighted_transactions,
                searcher=self.state.searcher,
            )
        assert len(self.data) == width - self.state.column_header_width, (
            f"Received data list is {len(self.data)} long instead of expected {width - self.state.column_header_width}"
        )

        byte_strings = [header.encode()] + [header_below.encode() for r in range(rows - 1)]
        attrs = [[(default_attr, len(row))] for row in byte_strings]

        # Determine which 'column' (refers to at least one column of the
        # screen, maybe more) should be highlighted if a selection has been
        # made.
        selection_start, selection_end = self.selection_start_end(self.state.cycles_per_char)
        start, end = self.start_end(self.state.cycles_per_char)
        first, last = self.first_last(self.state.cycles_per_char)

        non_empty_character_bytes = self.NON_EMPTY_CHAR.encode()
        character_bytes = self.FULL_CHAR.encode()
        for i, item in enumerate(self.data):
            has_focus = (selection_start <= i <= selection_end) != focus and self.state.has_focus
            more_left = self._more_left(self.data, i, first, start, end)
            more_right = self._more_right(self.data, i, last, start, end)
            empty_attr = f"{even_odd_focused(self.row_index, has_focus)}_event_row"
            character = character_bytes
            height = item
            if item < 0:
                height = 1
                character = non_empty_character_bytes

            if (more_left or more_right) and height == rows:
                height -= 1

            for l in range(height):
                byte_strings[l] += character
                color_idx = colors[i]
                attr = f"event_color_reversed_{color_idx}_{even_odd_focused(self.row_index, has_focus)}"
                if self.active_background is not None and (start <= i <= end):
                    attr += self.active_background
                attrs[l].append((attr, len(character)))

            for l in range(height, rows):
                if more_left:
                    byte_string = self.MORE_LEFT_CHAR.encode()
                elif more_right:
                    byte_string = self.MORE_RIGHT_CHAR.encode()
                else:
                    byte_string = self.EMPTY_CHAR.encode()

                attr = empty_attr
                if self.active_background is not None and (start <= i <= end):
                    attr += self.active_background
                byte_strings[l] += byte_string
                attrs[l].append((attr, len(byte_string)))

        return attrs, byte_strings

    def rows(self, size: tuple[int], focus: bool = False) -> int:
        return max(1, self.ed.max_events_per_time()) if self.expanded else 1

    def render(
        self,
        size: tuple[()] | tuple[int] | tuple[int, int],
        focus: bool = False,
    ) -> urwid.canvas.Canvas:
        maxcol = size[0]

        has_focus = focus and self.state.has_focus
        default_attr = f"{even_odd_focused(self.row_index, has_focus)}_event_row"

        if self.expanded:
            rows = max(1, self.ed.max_events_per_time())
            if self.state.cycles_per_char.numerator == 1:
                attrs, lines = self.get_expanded_lines_show_abbrev(rows, maxcol, default_attr, focus)
            else:
                attrs, lines = self.get_expanded_lines(rows, maxcol, default_attr, focus)
        else:
            attrs, lines = self.get_condensed_line(maxcol, default_attr, focus)
        return urwid.canvas.TextCanvas(lines, attrs)

    @property
    def _transaction_row(self) -> bool:
        return isinstance(self.ed, TransactionEventData)

    def _make_selection(self, **kwargs: Any) -> Selection:
        return Selection(event_row=self.ed.key(), within_transaction=self._transaction_row, **kwargs)

    def _adjust_selection(self, event: Event, **kwargs: Any) -> Selection:
        return self.state.selection.adjust_within_row(event, duration=self.state.ps_per_cycle, **kwargs)

    def keypress(
        self,
        size: tuple[()] | tuple[int] | tuple[int, int],
        key: str,
    ) -> str | None:
        if not self.state.selection or not self.state.selection.is_selected(self.ed):
            if key in action_keypresses[ACTIONS.EVENT_ROW_SELECT_FIRST_EVENT]:
                for event in self.ed[self.state.start_ps :]:
                    self.on_make_selection(self._make_selection(event=event, duration=self.state.ps_per_cycle))
                    return None
            return key

        if self.state.selection.is_event() and key in action_keypresses[ACTIONS.EVENT_ROW_NEXT_EVENT]:
            oldest_younger = self.ed.oldest_younger(self.state.selection.event)
            if oldest_younger is not None:
                self.on_make_selection(self._adjust_selection(oldest_younger))
                return None
        elif self.state.selection.is_event() and key in action_keypresses[ACTIONS.EVENT_ROW_PREV_EVENT]:
            youngest_older = self.ed.youngest_older(self.state.selection.event)
            if youngest_older is not None:
                self.on_make_selection(self._adjust_selection(youngest_older))
                return None

        return key

    def mouse_to_selection(self, col: int, row: int) -> Selection:
        """
        Given a column and row within this widget, return a Selection object
        representing the events contained within that cell given the current
        display state. The returned Selection object may reference a single
        event, a row/time region, or neither.
        """
        col_idx = col - self.state.column_header_width
        difference_ps = self.state.start_ps % self.state.ps_per_cycle
        difference_chars = math.floor(difference_ps / self.state.ps_per_char)

        def ps_range_to_selection(start_ps: int, end_ps: int) -> Selection:
            it = self.ed[start_ps:end_ps]
            first_event = next(it, None)
            second_event = next(it, None)
            if second_event:
                # If at least two events, select the entire region
                return self._make_selection(time_range=(start_ps, end_ps))
            if first_event:
                # If only one event, select only that event
                return self._make_selection(event=first_event, duration=self.state.ps_per_cycle)
            # If no events, return an empty selection
            return Selection()

        if not self.expanded:
            if self.state.cycles_per_char >= 1:
                start_ps, end_ps = self.index_to_ps_range(col_idx, self.state.cycles_per_char)
            else:
                cycle_index = math.floor((col_idx + difference_chars) * self.state.cycles_per_char)
                start_ps, end_ps = self.index_to_ps_range(cycle_index, 1)

            return ps_range_to_selection(start_ps, end_ps)
        if self.state.cycles_per_char > 1:
            assert self.state.cycles_per_char.denominator == 1

            # Do not select an event if the user clicked on an 'empty'
            # portion of the column. If the value in self.data is <0, it
            # means we are displaying a "non-empty character" here with a
            # height of 1.
            max_row = 1 if self.data[col_idx] < 0 else self.data[col_idx]
            if row >= max_row:
                return Selection()  # nothing there, return empty selection

            # Convert column to the bounds of this cell, in picoseconds
            col_start_ps, col_end_ps = self.index_to_ps_range(col_idx, self.state.cycles_per_char)
            return ps_range_to_selection(col_start_ps, col_end_ps)
        # Convert column to an index into the data array (only
        # guaranteed to exist and be valid when the current view is
        # expanded with cycles_per_char <= 1)
        data_idx = math.floor((col_idx + difference_chars) * self.state.cycles_per_char)
        assert data_idx >= 0 and data_idx < len(self.data)
        if row >= len(self.data[data_idx]):
            return Selection()  # nothing there, return empty selection
        event = self.data[data_idx][row]
        return self._make_selection(event=event, duration=self.state.ps_per_cycle)

    def mouse_to_next_selection(self, col: int, reverse: bool = False) -> Selection:
        if self.expanded:
            col_idx = col - self.state.column_header_width
            start_ps, _end_ps = self.index_to_ps_range(col_idx, self.state.cycles_per_char)
            event = self.ed.closest_to(start_ps, direction="backwards" if reverse else "forwards")
            if event:
                return self._make_selection(event=event, duration=self.state.ps_per_cycle)

        return Selection()

    def mouse_event(
        self,
        size: tuple[()] | tuple[int] | tuple[int, int],
        event: str,
        button: int,
        col: int,
        row: int,
        focus: bool,
    ) -> bool | None:
        # Ignore clicks to the left of where we're displaying events
        if col < self.state.column_header_width:
            return False

        event_button = (event, button)
        if event_button in action_mouseevents[ACTIONS.EVENT_ROW_SELECT_EVENT]:
            selection = self.mouse_to_selection(col, row)
            if selection:
                self.on_make_selection(selection)
        elif event_button in action_mouseevents[ACTIONS.EVENT_ROW_EXTEND_SELECTION]:
            selection = self.mouse_to_selection(col, row)
            if selection and self.state.selection:
                self.on_extend_selection(selection)
        elif event_button in action_mouseevents[ACTIONS.EVENT_ROW_NEAREST_NEXT_EVENT]:
            selection = self.mouse_to_next_selection(col)
            if selection:
                self.on_make_selection(selection)
        elif event_button in action_mouseevents[ACTIONS.EVENT_ROW_NEAREST_PREV_EVENT]:
            selection = self.mouse_to_next_selection(col, reverse=True)
            if selection:
                self.on_make_selection(selection)
        return None
