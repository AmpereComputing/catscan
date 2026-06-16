# Copyright (c) 2024 Ampere Computing. All rights reserved.
# SPDX-License-Identifier: BSD-3-Clause

from typing import Any

from catscan.data import Event, EventStreamDataTransactionView, TransactionEventData
from catscan.widgets.event_row import EventRow
from catscan.widgets.event_view import EventView


class TransactionEventRow(EventRow):
    LEVEL_CHARS = [
        "+",
        "↳",
        "-",
    ]

    @property
    def level(self) -> int:
        return self.ed.level

    @property
    def level_char(self) -> str:
        return self.LEVEL_CHARS[self.level % len(self.LEVEL_CHARS)]

    def get_column_header(self) -> str:
        return (
            " " * self.level
            + f"{self.level_char} {self.ed.short_name:<{self.state.column_header_width - (3 + self.level)}}│"[
                -self.state.column_header_width :
            ]
        )


class TransactionView(EventView):
    """
    Display rows of transactions with their respective events.
    """

    def data_view(self, **kwargs: Any) -> EventStreamDataTransactionView:
        return self.stream_data.transaction_events(**kwargs)

    def iter_event_rows(self, **kwargs: Any) -> EventStreamDataTransactionView:
        return self.stream_data.transaction_events(**kwargs)

    def create_row(self, row: TransactionEventData, row_index: int, **kwargs: Any) -> TransactionEventRow:
        return TransactionEventRow(
            row,
            self.state,
            row_index,
            on_make_selection=self.on_make_selection,
            on_extend_selection=self.on_extend_selection,
            active_background="_active",
            **kwargs,
        )

    def max_column_header_width(self) -> int:
        if self.stream_data.transaction_event_rows:
            return max(len(str(t.name)) + t.level for t in self.stream_data.transaction_event_rows.values()) + 3
        return 3

    def _update_selected_row_name(self, newly_selected_row: int) -> None:
        for i, row_name in enumerate(self.stream_data.transaction_event_rows.keys()):
            if newly_selected_row == row_name:
                self.list_walker.set_focus(i)
                return
