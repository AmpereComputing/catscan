# Copyright (c) 2024 Ampere Computing. All rights reserved.
# SPDX-License-Identifier: BSD-3-Clause

from collections.abc import Callable
from typing import Any, Literal

from perf_streams.event_stream import Event

from catscan.commit_sync import CommitSyncer
from catscan.data import EventStreamDataTransactionView, TransactionEventData
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

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self._commit_sync_candidates_by_row: dict[str | int, list[int]] = {}
        self._commit_sync_row_by_index: dict[int, str | int] = {}
        self._suppress_commit_sync = False
        super().__init__(*args, **kwargs)

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

    def start_commit_sync(self, commit_syncer: CommitSyncer, commit_event: str, commit_data_name: str) -> list[Event]:
        super().start_commit_sync(commit_syncer, commit_event, commit_data_name)
        self._commit_sync_candidates_by_row = {}
        self._commit_sync_row_by_index = {}
        for txid, transaction_row in self.stream_data.transaction_event_rows.items():
            candidates = []
            for event in transaction_row[:]:
                if event.name == commit_event and commit_data_name in event.data:
                    sync_index = event.data[commit_data_name]
                    candidates.append(sync_index)
                    self._commit_sync_row_by_index[sync_index] = txid
            if candidates:
                self._commit_sync_candidates_by_row[txid] = candidates
        return []

    def stop_commit_sync(self) -> None:
        super().stop_commit_sync()
        self._commit_sync_candidates_by_row = {}
        self._commit_sync_row_by_index = {}
        self._suppress_commit_sync = False

    def commit_sync_index_candidates(self) -> list[int]:
        return self._commit_sync_candidates_by_row.get(self.focused_row_key(), [])

    def commit_sync_row(self, sync_index: int) -> str | int | None:
        return self._commit_sync_row_by_index.get(sync_index)

    def commit_sync_position(self) -> tuple[str | int | None, str | int | None]:
        return self.top_visible_row_key(), self.focused_row_key()

    def restore_commit_sync_position(self, position: tuple[str | int | None, str | int | None]) -> None:
        top_row_key, focused_row_key = position
        if top_row_key is None and focused_row_key is None:
            return

        def restore_position() -> None:
            top_position = None
            if top_row_key is not None:
                top_position = self.row_position(top_row_key)
                if top_position is not None:
                    self.list_box.set_focus(top_position)
                    self.list_box.set_focus_valign("top")

            if focused_row_key is not None and focused_row_key != top_row_key:
                focused_position = self.row_position(focused_row_key)
                if focused_position is not None:
                    coming_from = "above" if top_position is None or focused_position >= top_position else "below"
                    self.list_box.set_focus(focused_position, coming_from)

            self._emit_position_change_notifications(force=True)
            self._invalidate()

        self._with_commit_sync_suppressed(restore_position)

    def scroll_row_to_edge_for_commit_sync(self, row_key: str | int, align: Literal["top", "bottom"] = "top") -> bool:
        scrolled = False

        def scroll() -> None:
            nonlocal scrolled
            scrolled = self.scroll_row_to_edge(row_key, align)

        self._with_commit_sync_suppressed(scroll)
        return scrolled

    def scroll_row_to_top_for_commit_sync(self, row_key: str | int) -> bool:
        return self.scroll_row_to_edge_for_commit_sync(row_key, "top")

    @property
    def suppressing_commit_sync(self) -> bool:
        return self._suppress_commit_sync

    def _with_commit_sync_suppressed(self, operation: Callable[[], None]) -> None:
        self._suppress_commit_sync = True
        try:
            operation()
        finally:
            self._suppress_commit_sync = False

    def _update_selected_row_name(self, newly_selected_row: int) -> None:
        row_position = self.row_position(newly_selected_row)
        if row_position is not None:
            self.list_walker.set_focus(row_position)
            self._emit_position_change_notifications()
