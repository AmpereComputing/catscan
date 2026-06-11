# Copyright (c) 2024 Ampere Computing. All rights reserved.
# SPDX-License-Identifier: BSD-3-Clause

from typing import Any

import urwid

from catscan.data import EventData, EventStreamData, EventStreamDataEventView
from catscan.widgets.event_row import EventRow, EventRowBase, GroupRow
from catscan.widgets.event_view import EventView

# TODO implement a version of a scrollable list box that scrolls sensibly with
# the mouse wheel (i.e. similarly to what page up/page down to with the current
# list box). The problem with urwid.ListBox is that in order to get the
# viewable area to move downwards if focus is at the top item in the list, you
# must scroll long enough to shift the focus downwards through all the viewable
# list items, and only when the focus would go off the bottom of the screen
# will the viewable area shift downwards.


class ResourceView(EventView):
    """
    Display rows of events in a scrollable container. If the view is zoomed out
    far enough or 'condensed', each character may represent multiple cycles or
    multiple events.
    """

    def __init__(
        self,
        *args: Any,
        include_groups: bool = True,
        **kwargs: Any,
    ) -> None:
        self._include_groups = include_groups
        super().__init__(*args, **kwargs)

    @property
    def has_groups(self) -> bool:
        return self._include_groups

    def _create_rows_around_index(self, index: int, around: int = 0) -> list[EventRowBase]:
        total_rows = self.total_rows()
        if index >= total_rows or index < 0:
            return []

        rows = self.iter_event_rows()
        start = max(0, index - around)
        stop = min(total_rows, index + around + 1)
        groups_seen = set()
        included_rows = []
        for row_index, row in enumerate(rows):
            if self._include_groups and row.group not in groups_seen:
                if start <= (row_index + len(groups_seen)) < stop:
                    included_rows.append(self.create_group_row(row.group, row_index + len(groups_seen)))
                groups_seen.add(row.group)

            if start <= (row_index + len(groups_seen)) < stop:
                included_rows.append(self.create_row(row, row_index + len(groups_seen)))
            elif row_index + len(groups_seen) >= stop:
                break

        return included_rows

    def create_row(self, row: EventData, row_index: int, **kwargs: Any) -> EventRow:
        return EventRow(
            row,
            self.state,
            row_index,
            on_make_selection=self.on_make_selection,
            on_extend_selection=self.on_extend_selection,
            **kwargs,
        )

    def create_group_row(self, group: str, row_index: int, **kwargs: Any) -> GroupRow:
        return GroupRow(group, self.state, row_index, **kwargs)

    def add_rows(self):
        groups_seen = set()
        for row_index, row in enumerate(self.iter_event_rows()):
            if self._include_groups and row.group not in groups_seen:
                self.list_walker.append(self.create_group_row(row.group, row_index + len(groups_seen)))
                groups_seen.add(row.group)
            self.list_walker.append(self.create_row(row, row_index + len(groups_seen)))

    def max_column_header_width(self) -> int:
        if self.stream_data.event_rows:
            max_event_name = max(
                map(
                    EventRow.max_column_header_width, [row.short_name for _, row in self.stream_data.event_rows.items()]
                )
            )
            if self._include_groups:
                max_group_name = max(
                    map(GroupRow.max_column_header_width, [row.group for _, row in self.stream_data.event_rows.items()])
                )
            else:
                max_group_name = 0
            return max(max_event_name, max_group_name)
        return 1

    def _update_selected_row_name(self, newly_selected_row: str) -> None:
        groups_seen = set()
        for row_index, row in enumerate(self.stream_data):
            if self._include_groups and row.group not in groups_seen:
                groups_seen.add(row.group)
            if newly_selected_row == row.name:
                self.list_walker.set_focus(row_index + len(groups_seen))
                return


class SubsetResourceView(ResourceView):
    """Fixed size, subset of events in normal resource view."""

    def __init__(
        self,
        *args: Any,
        max_rows: int = 16,
        include_groups: bool = False,
        allow_row_expansion: bool = False,
        **kwargs: Any,
    ) -> None:
        self.events = []
        self._max_rows = max_rows
        self._allow_row_expansion = allow_row_expansion
        super().__init__(*args, include_groups=include_groups, length_hint=max_rows, **kwargs)

    def iter_event_rows(self, **kwargs: Any) -> EventStreamDataEventView:
        return self.data_view(keys=self.events, **kwargs)

    def empty(self) -> bool:
        return not self.events

    def add_event(self, event: str):
        if event not in self.events:
            self.events.append(event)
            self.update_rows()

    def remove_event(self, event: str):
        try:
            index = self.events.index(event)
            del self.events[index]
            self.update_rows()
        except (IndexError, ValueError):
            pass

    def remove_focused_event(self):
        self.remove_event(self.focused_row()[1])

    def remove_all_events(self):
        self.events = []
        self.update_rows()

    def update_stream_data(self, stream_data: EventStreamData) -> None:
        super().update_stream_data(stream_data)
        all_events = {row.name for row in self.stream_data.events()}
        new_events = [event for event in self.events if event in all_events]
        if self.events != new_events:
            self.events = new_events
            self.update_rows()

    def create_row(self, row: EventData, row_index: int, **kwargs: Any) -> EventRow:
        return EventRow(
            row,
            self.state,
            row_index,
            on_make_selection=self.on_make_selection,
            on_extend_selection=self.on_extend_selection,
            expanded_allowed=self._allow_row_expansion,
            **kwargs,
        )

    def max_rows(self) -> int | None:
        return self._max_rows

    def _update_selected_row_name(self, newly_selected_row: str) -> None:
        for i, row_name in enumerate(self.events):
            if newly_selected_row == row_name:
                self.list_walker.set_focus(i)
                return

    def rows(self, size: tuple[int], focus: bool = False) -> int:
        if not self.events:
            return 0
        rows = size[0]
        return min(sum(row.rows(size) for row in self.list_walker), rows)

    def pack(self, size: tuple[()] | tuple[int] | tuple[int, int], focus: bool = False) -> tuple[int, int]:
        if len(size) == 2:
            maxcol, maxrow = size
        elif len(size) == 1:
            maxcol = size[0]
            maxrow = self.max_rows()
        else:
            return (None, None)

        return (maxcol, self.rows((maxrow,), focus=focus))

    def _adjust_size(self, size: tuple[()] | tuple[int] | tuple[int, int], focus: bool = False) -> tuple[int, int]:
        if len(size) == 1:
            size = (size[0], self.rows((self.max_rows(),), focus=focus))
        return size

    def render(
        self,
        size: tuple[()] | tuple[int] | tuple[int, int],
        focus: bool = False,
    ) -> urwid.canvas.Canvas:
        return super().render(self._adjust_size(size, focus=focus), focus=focus)

    def mouse_event(
        self,
        size: tuple[()] | tuple[int] | tuple[int, int],
        event: str,
        button: int,
        col: int,
        row: int,
        focus: bool,
    ) -> bool | None:
        return super().mouse_event(self._adjust_size(size, focus=focus), event, button, col, row, focus)

    def keypress(
        self,
        size: tuple[()] | tuple[int] | tuple[int, int],
        key: str,
    ) -> str | None:
        return super().keypress(self._adjust_size(size), key)

    def sizing(self) -> set[urwid.Sizing]:
        return frozenset([urwid.Sizing.FLOW, urwid.Sizing.BOX, urwid.Sizing.FIXED])
