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
    LEVEL_CHARS = (
        "+",
        "↳",
        "-",
    )

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

    def __init__(self, *args: Any, on_viewport_change: Callable | None = None, **kwargs: Any) -> None:
        self._commit_sync_candidates_by_row: dict[str | int, list[int]] = {}
        self._commit_sync_row_by_index: dict[int, str | int] = {}
        self._suppress_commit_sync = False
        self.on_viewport_change = on_viewport_change
        self._last_top_visible_row_key = None
        self._last_top_visible_position = None
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

    def update_rows(self) -> None:
        """Rebuild transaction rows and reset tracked viewport position."""
        super().update_rows()
        self._last_top_visible_row_key = None
        self._last_top_visible_position = None

    def _emit_position_change_notifications(self, user: bool = False, force: bool = False) -> None:
        top_position = self._visible_top_position()
        movement_alignment = self._movement_alignment(top_position)
        self._emit_viewport_change_if_needed(top_position, movement_alignment, force=force)
        self._emit_focus_change_if_needed(movement_alignment, user=user, force=force)

    def _visible_top_position(self) -> int | None:
        if self._last_rendered_size is None or len(self.list_box.body) == 0:
            return None

        middle, top, _bottom = self.list_box.calculate_visible(self._last_rendered_size, self.has_focus())
        focus_offset, _focus_inset = self.list_box.get_focus_offset_inset(self._last_rendered_size)
        if focus_offset == 0 or top.trim > 0:
            return middle.focus_pos
        if top.fill:
            return top.fill[-1].position
        return middle.focus_pos

    def top_visible_row_key(self) -> str | int | None:
        """Return the row key at the top of the visible transaction viewport."""
        return self._row_key_at_position(self._visible_top_position())

    def _movement_alignment(self, top_position: int | None) -> Literal["before", "after"] | None:
        if (
            top_position is not None
            and self._last_top_visible_position is not None
            and top_position != self._last_top_visible_position
        ):
            return "after" if top_position > self._last_top_visible_position else "before"
        return None

    def _emit_viewport_change_if_needed(
        self,
        top_position: int | None = None,
        movement_alignment: Literal["before", "after"] | None = None,
        force: bool = False,
    ) -> None:
        if top_position is None:
            top_position = self._visible_top_position()
        if movement_alignment is None:
            movement_alignment = self._movement_alignment(top_position)

        top_row_key = self._row_key_at_position(top_position)
        if force or top_row_key != self._last_top_visible_row_key:
            self._last_top_visible_row_key = top_row_key
            self._last_top_visible_position = top_position
            if self.on_viewport_change is not None:
                self.on_viewport_change(self, movement_alignment)
        else:
            self._last_top_visible_position = top_position

    def max_column_header_width(self) -> int:
        if self.stream_data.transaction_event_rows:
            return max(len(str(t.name)) + t.level for t in self.stream_data.transaction_event_rows.values()) + 3
        return 3

    def start_commit_sync(self, commit_syncer: CommitSyncer, commit_event: str, commit_data_name: str) -> list[Event]:
        """Prepare transaction commit sync indexes for row-based anchoring."""
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
        """Clear transaction commit sync indexes and notification suppression."""
        super().stop_commit_sync()
        self._commit_sync_candidates_by_row = {}
        self._commit_sync_row_by_index = {}
        self._suppress_commit_sync = False

    def commit_sync_index_candidates(self) -> list[int]:
        """Return sync indexes found in the focused transaction row."""
        return self._commit_sync_candidates_by_row.get(self.focused_row_key(), [])

    def commit_sync_row(self, sync_index: int) -> str | int | None:
        """Return the transaction row containing a sync index, if known."""
        return self._commit_sync_row_by_index.get(sync_index)

    def commit_sync_position(self) -> tuple[str | int | None, str | int | None]:
        """Return the current top and focused row keys for restoration."""
        return self.top_visible_row_key(), self.focused_row_key()

    def restore_commit_sync_position(self, position: tuple[str | int | None, str | int | None]) -> None:
        """Restore the visible transaction rows captured for commit sync."""
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
        """Scroll during inbound commit sync without echoing another update."""
        scrolled = False

        def scroll() -> None:
            nonlocal scrolled
            scrolled = self.scroll_row_to_edge(row_key, align)

        self._with_commit_sync_suppressed(scroll)
        return scrolled

    def scroll_row_to_top_for_commit_sync(self, row_key: str | int) -> bool:
        """Scroll a commit sync row to the top of the viewport."""
        return self.scroll_row_to_edge_for_commit_sync(row_key, "top")

    @property
    def suppressing_commit_sync(self) -> bool:
        """Return whether transaction sync notifications are suppressed."""
        return self._suppress_commit_sync

    def _with_commit_sync_suppressed(self, operation: Callable[[], None]) -> None:
        self._suppress_commit_sync = True
        try:
            operation()
        finally:
            self._suppress_commit_sync = False
