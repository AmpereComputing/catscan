# Copyright (c) 2024 Ampere Computing. All rights reserved.
# SPDX-License-Identifier: BSD-3-Clause

from collections.abc import Callable

import urwid

from catscan.data import EventStreamData, TransactionEventData
from catscan.state import CatscanState
from catscan.summary import generate_summary_table
from catscan.widgets.button import ReleaseButton
from catscan.widgets.text_table import TextTable


class SummaryTable(urwid.WidgetWrap):
    def __init__(
        self,
        state: CatscanState,
        stream_data: EventStreamData,
    ) -> None:
        self.state = state
        self.stream_data = stream_data
        self.update_table_contents()
        self.table = TextTable(self.header, self.contents, self.footer, self.title)

        super().__init__(self.table)

    def update_table_contents(self) -> None:
        """
        Update table title, header, contents, and footer to match the current
        stream data and user selection.
        """
        if not self.state.selection.is_time_range():
            # This should only really happen if/when this isn't visible anyway
            self.header, self.contents, self.footer, self.title = [], [], [], ""
            return

        data_view = (
            self.stream_data.transaction_events()
            if self.state.selection.within_transaction()
            else self.stream_data.events()
        )

        self.header, self.contents, self.footer = generate_summary_table(
            data_view,
            self.state.selection.start_ps,
            self.state.selection.end_ps,
            [self.state.selection.event_row],
            None,
        )

        # Generate the title for the table
        picosecond_span = self.state.selection.end_ps - self.state.selection.start_ps
        cycle_span = picosecond_span // self.state.ps_per_cycle
        row_name = data_view.name_of(self.state.selection.event_row)
        self.title = f"Summary of {row_name} over {cycle_span} cycles ({picosecond_span} ps):"

    def update_state(self, new_state: CatscanState) -> None:
        if new_state == self.state:
            return

        new_selection = self.state.selection != new_state.selection
        self.state = new_state
        if new_selection:
            self.update_table_contents()
            self.table.set_contents(self.header, self.contents, self.footer, self.title)
            self._invalidate()

    def update_stream_data(self, stream_data: EventStreamData) -> None:
        self.stream_data = stream_data
        self.update_table_contents()
        self.table.set_contents(self.header, self.contents, self.footer, self.title)
        self._invalidate()


class SummarySidebar(urwid.WidgetWrap):
    """
    This widget display a summary of the current selection as a histogram of
    the selected events' abbreviations.
    """

    def __init__(
        self,
        state: CatscanState,
        stream_data: EventStreamData,
        on_clear_selection: Callable,
    ) -> None:
        self.on_clear_selection = on_clear_selection

        self.table = SummaryTable(state, stream_data)
        self.close_button = urwid.Padding(
            urwid.AttrMap(ReleaseButton("Close", align="center", on_press=self.clear_selection), "button"),
            align="center",
            width=9,
        )
        self.list_walker = urwid.SimpleListWalker([self.table, urwid.Text(""), self.close_button])
        self.list_box = urwid.ListBox(self.list_walker)
        self.scrollbar = urwid.ScrollBar(self.list_box, trough_char="│")

        super().__init__(self.scrollbar)

    def update_state(self, new_state: CatscanState) -> None:
        self.table.update_state(new_state)

    def update_stream_data(self, stream_data: EventStreamData) -> None:
        self.table.update_stream_data(stream_data)
        self._invalidate()

    def clear_selection(self, _widget) -> bool:
        return self.on_clear_selection()

    def needs_scrollbar(self, size: tuple[int, int]) -> bool:
        (maxcol, maxrow) = size
        return self.table.rows((maxcol,)) + 1 > maxrow
