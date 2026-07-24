# Copyright (c) 2024 Ampere Computing. All rights reserved.
# SPDX-License-Identifier: BSD-3-Clause

import logging
import os
import re
from argparse import Namespace
from asyncio import Future
from collections import Counter
from collections.abc import Callable, Iterable, Sequence
from enum import StrEnum, auto
from fractions import Fraction
from sys import maxsize
from typing import Any

import urwid
from perf_streams.event_stream import Event

from catscan.commands import Commands, DefaultCommands, ZoomTypes, command_definitions, parse_command_args
from catscan.commit_sync import CommitSyncer, CommitSyncState, build_commit_index, build_pushout_index
from catscan.data import (
    NUM_EVENT_COLORS,
    DataView,
    EventData,
    EventStreamData,
    EventStreamDataTransactionView,
    Transaction,
    get_event_data,
    save_event_data,
)
from catscan.events import EventSpecification, trace_events
from catscan.events.mapping import Mapper
from catscan.search import (
    EventStreamDataSearch,
    FilteringSearcher,
    IntSearcher,
    MatchType,
    PerPeriodSearcher,
    TextSearcher,
)
from catscan.state import CatscanState, HashableFrozenDict, Selection
from catscan.summary import generate_summary_table
from catscan.user_input import (
    ACTIONS,
    DOUBLE_CLICK_TIMEOUT,
    KEYPRESS_COMBINATION_TIMEOUT,
    action_keypresses,
    action_mouseevents,
)
from catscan.util import glob_to_pattern, hex_args_to_re
from catscan.widgets.ampere_logo import AmpereLogo
from catscan.widgets.event_row import RowType
from catscan.widgets.event_sidebar import EventDetail
from catscan.widgets.event_view import EventView, PrimarySplitEventView, RowViews, View
from catscan.widgets.popups import Help, Messages
from catscan.widgets.resource_view import ResourceView, SubsetResourceView
from catscan.widgets.separators import MiddleBorder
from catscan.widgets.status_bar import StatusBar
from catscan.widgets.summary_sidebar import SummarySidebar
from catscan.widgets.text_table import TextTable
from catscan.widgets.time_row import TimeRow
from catscan.widgets.transaction_view import TransactionView


class DefaultViews(StrEnum):
    """Default view names."""

    MAIN = auto()
    RESOURCE = auto()
    TRANSACTION = auto()
    PINNED = auto()


class Top(urwid.widget.Widget):
    """
    This widget makes up the top-level user-interface for Catscan. It is
    responsible for handling callbacks from all children widgets, maintaining
    the master copy of the state (CatscanState) and distributing any changes to
    that state to all children, passing mouse/keypress events to the
    appropriate children or handling them itself, and rendering the appropriate
    children widgets in the right places.
    """

    _sizing = frozenset(["box"])
    _selectable = True
    _max_cycles_per_char = Fraction(4096, 1)
    _min_cycles_per_char = Fraction(1, 32)
    MAX_SIMULTANEOUS_TXID_HIGHLIGHTS = 32

    def __init__(self, args: Namespace):
        self._infer_period = args.period is None
        self.state = CatscanState(
            has_focus=True,
            loading=True,
            ps_per_cycle=args.period or 1,
            column_header_width=1,
            cycles_per_char=Fraction(1, 1),
            start_ps=0,
            start_row=1,
            expand_rows=False,
            selection=Selection(),
            sort_event_keys=args.sort_keys,
            highlighted_transactions=HashableFrozenDict(),
            marked_events=HashableFrozenDict(),
            searcher=None,
            messages=[],
            show_help=False,
        )

        self.marking = False  # True if the last keypress began the process of
        # marking an event
        self.going_to_mark = False  # True if the last keypress began the
        # process of going to a marked event

        self.recent_unhandled_keys = []
        self.unhandled_keys_timer = None
        self.last_mouse_press_button = 0
        self.last_mouse_release_button = 0
        self.last_mouse_release_time = None

        self.commit_sync_event = args.instruction_commit_event
        self.commit_sync_data_name = args.instruction_commit_index
        self.commit_sync_index = {}
        self.commit_syncer = None

        self.loading_pct = 0
        self.loading_thread = None

        self.post_load_commands = args.onload_command
        self.init_sync_commands = args.onsync_command

        self.hexargs_re = hex_args_to_re(args.hex)

        initial_esd = EventStreamData.create_empty()

        self.messages = Messages(
            self.state,
            on_close=self.clear_messages,
            desired_width=120,
            desired_height=30,
        )
        self.help = Help(on_close=self.hide_help, desired_width=80, desired_height=60)
        self.time_header = TimeRow(initial_esd, self.state)

        self.event_details = EventDetail(
            self.state,
            initial_esd,
            args.hex,
            on_clear_selection=self.clear_selection,
            on_highlight_transactions=self.highlight_transactions,
            on_unhighlight_transactions=self.unhighlight_transactions,
            on_go_to_transactions=self.go_to_transactions,
            on_search=self.search,
        )
        self.summary_sidebar = SummarySidebar(self.state, initial_esd, on_clear_selection=self.clear_selection)
        self.loading_screen = AmpereLogo()
        self.status_bar = StatusBar(
            self.state, on_search=lambda args: self.command(f"search {args}"), on_command=self.command
        )

        if args.view == DataView.TRANSACTIONS:
            self.view_rows = RowViews(
                self.state,
                PrimarySplitEventView(
                    DefaultViews.MAIN, self._create_view(TransactionView, DefaultViews.TRANSACTION, initial_esd)
                ),
            )
        else:
            self.view_rows = RowViews(
                self.state,
                PrimarySplitEventView(
                    DefaultViews.MAIN,
                    self._create_view(
                        ResourceView,
                        DefaultViews.RESOURCE,
                        initial_esd,
                    ),
                    self._create_view(
                        SubsetResourceView,
                        DefaultViews.PINNED,
                        initial_esd,
                    ),
                    primary_key_actions={
                        ACTIONS.SCROLL_PAGE_UP,
                        ACTIONS.SCROLL_PAGE_DOWN,
                        ACTIONS.SCROLL_HALF_PAGE_UP,
                        ACTIONS.SCROLL_HALF_PAGE_DOWN,
                        ACTIONS.SCROLL_TOP,
                        ACTIONS.SCROLL_BOTTOM,
                        ACTIONS.TOP_FOCUS,
                        ACTIONS.CENTER_FOCUS,
                        ACTIONS.BOTTOM_FOCUS,
                    },
                ),
            )
        self._update_view_rows()

        self.view_frame = urwid.Frame(self.view_rows, header=self.time_header)
        self.columns = urwid.Columns([self.loading_screen], min_width=20, dividechars=1)
        self.frame = urwid.Frame(self.columns, footer=self.status_bar)

        # To be set almost immediately after construction of Top to point to
        # the loop itself
        self.main_loop: urwid.MainLoop = None

    def _create_view(
        self, cls: type[View], name: str, initial_esd: EventStreamData, *args: Any, **kwargs: Any
    ) -> EventView:
        return cls(
            name,
            self.state,
            initial_esd,
            *args,
            on_zoom_in=self.zoom_in,
            on_zoom_out=self.zoom_out,
            on_scroll_left=self.scroll_left,
            on_scroll_right=self.scroll_right,
            on_toggle_expanded=self.toggle_expanded,
            on_make_selection=self.make_selection,
            on_extend_selection=self.extend_selection,
            on_translate_event=self.translate_event,
            **kwargs,
        )

    def _update_view_rows(self, invalidate: bool = True):
        self.view_rows.update_rows()
        if invalidate:
            self._invalidate()

    @property
    def _resource_view(self) -> ResourceView:
        return self.view_rows[f"{DefaultViews.MAIN}.{DefaultViews.RESOURCE}"]

    @property
    def _pinned_resource_view(self) -> SubsetResourceView:
        return self.view_rows[f"{DefaultViews.MAIN}.{DefaultViews.PINNED}"]

    @property
    def _focused_view(self) -> EventView:
        focused = self.view_rows.focused_views()
        if focused:
            return self.view_rows[focused[0]]
        return self._resource_view

    def _get_data_view_from_views(self, views: list[str] | None = None) -> EventView:
        # NOTE: currently only returns a single data-view as multiple are not
        # supported yet and not clear how they will be handled (if at all)
        views = views or self.view_rows.focused_views()
        data_views = [self.view_rows[view].data_view() for view in views]
        if not data_views:
            data_views = [self.stream_data.events()]
        return data_views[0]

    @property
    def _focused_data_view(self) -> EventView:
        return self._get_data_view_from_views()

    @property
    def sidebar_width(self) -> int:
        return max(min((self.cached_maxcol - self.state.column_header_width) // 2, 80), 0)

    @property
    def usable_width(self) -> int:
        """
        The number of columns devoted to the right-hand sidebar portion of the
        screen.
        """
        usable = self.cached_maxcol - self.state.column_header_width - 1
        if not self.state.selection:
            return usable
        return usable - self.sidebar_width - 1

    def get_status(self) -> tuple[str, str | None]:
        selected_event = self.state.selection.is_event()
        if self.marking:
            return ("🖍  provide a character to mark selected event...", self.stream_data.source)
        if self.going_to_mark:
            return ("👓  provide a character to go to marked event...", self.stream_data.source)
        if self.state.searcher is not None:
            if selected_event and self.state.selection.event == self.search_tracker.search_cursor:
                return (
                    f"🔍 {self.search_tracker.cursor_idx + 1} of {self.search_tracker.total_matches} results in {self.state.searcher}",
                    self.stream_data.source,
                )
            return (
                f"🔍 {self.search_tracker.total_matches} results for {self.state.searcher}",
                self.stream_data.source,
            )
        sync_status = ""
        if self.commit_syncer and not self.commit_syncer.stopped:
            symbol = "⇄" if self.commit_syncer.syncing else "⏸"
            sync_status = f" | {symbol} {self.commit_syncer.fifo_basename}"
        icon = "🐈" if self.state.has_focus else "⏾ "
        return (
            f"{icon} zoom (cycles/character): {self.state.cycles_per_char}{sync_status}",
            self.stream_data.source,
        )

    def load_file(
        self,
        filename: str,
        view: str,
        mapper: Mapper,
        event_filters: trace_events.EventFilters,
        events: list[EventSpecification],
        cache_to: str | None,
        post_to_tx: list[str] | None,
        pull_from_tx: list[str] | None,
        occupancy: list[str] | None,
        convert_enumerations: bool = True,
        **view_options: Any,
    ):
        if self.loading_thread is not None:
            # Attempt to cleanup a previous background thread if it still exits
            assert not self.state.loading, "Attempting to load a file when a previous load isn't finished"
            self.loading_thread.result()
            self.loading_thread = None

        self.update_state(self.state.copy_with(loading=True))
        self.loading_pct = 0

        def event_stream_pct_loaded(updater: int) -> Callable:
            def callback(percentage: float):
                self.loading_screen.update_pct_loaded(percentage)
                self.loading_pct = percentage
                os.write(updater, b"u")

            return callback

        def update(_data):
            self._invalidate()

        def event_stream_loaded(future: Future):
            self.update_state(self.state.copy_with(loading=False))
            self.update_stream_data(future.result())
            self.eval_commands(self.post_load_commands)

            # Force redrawing the screen since we're updating the state outside
            # of urwid's normal event loop
            self.main_loop.draw_screen()

        def event_stream_loading_error(message: str):
            self.add_message(message)
            # Force redrawing the screen since we're updating the state outside
            # of urwid's normal event loop
            self.main_loop.draw_screen()

        def load_data(
            filename: str,
            view: str,
            mapper: Mapper,
            event_filters: trace_events.EventFilters,
            events: list[EventSpecification],
            cache_to: str | None,
            post_to_tx: list[str],
            pull_from_tx: list[str],
            occupancy: list[str],
            update_pct_callback: Callable,
            error_callback: Callable,
            convert_enumerations: bool = True,
            **view_options: Any,
        ) -> EventStreamData:
            esd = get_event_data(
                filename,
                view,
                mapper=mapper,
                event_filters=event_filters,
                events=events,
                post_to_tx=post_to_tx,
                pull_from_tx=pull_from_tx,
                occupancy=occupancy,
                pct_loaded_callback=update_pct_callback,
                convert_enumerations=convert_enumerations,
                **view_options,
            )

            # If the user requested, save the loaded data to a python pickle
            # file for faster loading in the future
            if cache_to is not None:
                # `cache_to` is None if the user does not want caching, the
                # empty string if they want caching but didn't specify a path,
                # and a non-empty path if they want caching and specified a
                # path
                cache_filename = cache_to if len(cache_to) > 0 else filename
                try:
                    save_event_data(cache_filename, esd)
                except Exception as e:
                    error_callback(
                        f"Error: Exception occurred while attempting to save cached version of {filename} to {cache_filename}: {e}"
                    )

            return esd

        def load_now(_loop, _arg):
            updater = self.main_loop.watch_pipe(update)
            self.loading_thread = self.main_loop.event_loop.run_in_executor(
                None,
                load_data,
                filename,
                view,
                mapper,
                event_filters,
                events,
                cache_to,
                post_to_tx,
                pull_from_tx,
                occupancy,
                event_stream_pct_loaded(updater),
                event_stream_loading_error,
                convert_enumerations=convert_enumerations,
                **view_options,
            ).add_done_callback(event_stream_loaded)

        self.main_loop.set_alarm_in(0, load_now)

    def render(
        self,
        size: tuple[()] | tuple[int] | tuple[int, int],
        focus: bool = False,
    ) -> urwid.canvas.Canvas:
        (maxcol, maxrow) = size
        self.cached_maxcol = maxcol  # cache for event handling

        showing_messages = len(self.state.messages) > 0
        showing_popup = showing_messages or self.state.show_help

        if self.state.loading:
            self.status_bar.set_text(f"Loading ({self.loading_pct}%)...", None)
            canvas = self.frame.render(size, focus and not showing_popup)
        else:
            self.status_bar.set_text(*self.get_status())
            if self.state.selection:
                sidebar = self.event_details if self.state.selection.is_event() else self.summary_sidebar
                self.columns.contents = [
                    (self.view_frame, (urwid.WHSettings.WEIGHT, 1, False)),
                    (sidebar, (urwid.WHSettings.GIVEN, self.sidebar_width, False)),
                ]
                self.status_bar.sidebar_width = self.sidebar_width
                sidebar_maxrows = maxrow - self.status_bar.rows((maxcol,), False)
                self.status_bar.showing_scrollbar = sidebar.needs_scrollbar((self.sidebar_width, sidebar_maxrows))
            else:
                if len(self.columns.contents) > 1:
                    self.columns.contents = [(self.view_frame, (urwid.WHSettings.WEIGHT, 1, False))]
                self.status_bar.sidebar_width = None
                self.status_bar.showing_scrollbar = True
            canvas = self.frame.render(size, focus and not showing_popup)

        if self.state.show_help:
            canvas = self.help.overlay(canvas, size, focus)
        elif showing_messages:
            canvas = self.messages.overlay(canvas, size, focus)
        return canvas

    def update_stream_data(
        self, stream_data: EventStreamData, external_column_width: int = 0, zoom_to_extents: bool = True
    ) -> None:
        self.stream_data = stream_data

        ps_per_cycle = stream_data.period if self._infer_period else self.state.ps_per_cycle
        if self.commit_sync_event in self.stream_data.event_rows:
            self.commit_sync_index = build_commit_index(
                self.stream_data.event_rows[self.commit_sync_event], self.commit_sync_data_name
            )
            self.pushout_index = build_pushout_index(self.commit_sync_index, ps_per_cycle)
        else:
            self.commit_sync_index = {}
            self.pushout_index = {}

        self.time_header.update_stream_data(stream_data)
        self.view_rows.update_stream_data(stream_data)
        self.event_details.update_stream_data(stream_data)
        self.summary_sidebar.update_stream_data(stream_data)

        max_column_width = max([self.view_rows.max_column_header_width(), external_column_width])
        new_state = self.state.copy_with(
            column_header_width=max_column_width,
            start_row=1,
            selection=Selection(),
            ps_per_cycle=ps_per_cycle,
        )

        self.update_state(new_state)
        if zoom_to_extents:
            self.go_to_time_range(self.stream_data.first_time, self.stream_data.last_time)
        self._update_view_rows(invalidate=False)
        self._invalidate()
        self.columns.contents = [(self.view_frame, (urwid.WHSettings.WEIGHT, 1, False))]

    def update_state(self, new_state: CatscanState, external_sync: bool = False) -> bool:
        """
        Update the application state using a newly-supplied state, fix up any
        irregularities in alignment of the display, and flow the new state out
        to any children widgets which need it.

        If inum sync is enabled, this also handles sending an update to the
        connected sibling catscan process with our current inum-based
        'horizontal' position in time (but avoid doing this when this update is
        the result of another process sending us theirs)
        """
        # Ensure start_ps stays aligned to the number of picoseconds per character
        min_granularity = round(new_state.cycles_per_char * new_state.ps_per_cycle)
        if new_state.cycles_per_char.denominator > 1:
            difference = new_state.start_ps - round(round(new_state.start_ps / min_granularity) * min_granularity)
        else:
            difference = new_state.start_ps % min_granularity
        if difference > min_granularity / 2:
            new_state = new_state.copy_with(start_ps=new_state.start_ps + min_granularity - difference)
        elif difference > 0:
            new_state = new_state.copy_with(start_ps=new_state.start_ps - difference)

        if new_state != self.state:
            should_send_commit_sync = not external_sync and (
                new_state.ps_per_cycle != self.state.ps_per_cycle
                or new_state.column_header_width != self.state.column_header_width
                or new_state.cycles_per_char != self.state.cycles_per_char
                or new_state.expand_rows != self.state.expand_rows
                or new_state.start_ps != self.state.start_ps
            )

            self.state = new_state
            if new_state.selection:
                self.event_details.update_state(new_state)
                self.summary_sidebar.update_state(new_state)
            self.messages.update_state(new_state)
            self.time_header.update_state(new_state)
            self.view_rows.update_state(new_state)
            self.status_bar.update_state(new_state)

            if new_state.expand_rows != self.state.expand_rows:
                self._update_view_rows(invalidate=False)
            self._invalidate()

            if should_send_commit_sync:
                self.send_commit_sync()

            return True
        return False

    def next_row_id(self) -> int:
        """
        Return the first row ID (as an index into
        EventStreamData.event_row_keys) after the currently-focused row. This
        is typically useful when you need a place to start searching for some
        UI action relative to the user's current location.
        """
        data_view = self._focused_data_view

        def group_index(name: str) -> int:
            return min([data_view.keys().index(row.name) for row in data_view if row.group == name])

        event_row_indices = [data_view.keys().index(row) + 1 for row in self.view_rows.focused_event_rows()]
        group_row_indices = [group_index(group) for group in self.view_rows.focused_group_rows()]
        combined_indices = event_row_indices + group_row_indices

        if combined_indices:
            return min(combined_indices)
        return 0

    def show_help(self, command: str | None = None) -> bool:
        if command:
            self.help.filter_by_command(command)
        else:
            self.help.reset_filter()

        new_state = self.state.copy_with(show_help=True)
        return self.update_state(new_state)

    def hide_help(self) -> bool:
        new_state = self.state.copy_with(show_help=False)
        return self.update_state(new_state)

    def add_message(self, message: str) -> bool:
        new_state = self.state.copy_with(messages=self.state.messages + [message])
        return self.update_state(new_state)

    def clear_messages(self) -> bool:
        new_state = self.state.copy_with(messages=[])
        return self.update_state(new_state)

    def toggle_expanded(self, event_name: str) -> bool:
        # TODO only toggle the expansion of the selected event row
        new_state = self.state.copy_with(expand_rows=not self.state.expand_rows)
        return self.update_state(new_state)

    def _zoom_around(self, new_cycles_per_char: Fraction, column: int) -> CatscanState:
        assert 1 in (new_cycles_per_char.numerator, new_cycles_per_char.denominator), (
            "Zoom level *must* contain 1 in either numerator or denominator"
        )

        # First, get the current time at column we are attempting to keep in place
        data_columns = max(0, column - self.state.column_header_width)
        new_start_ps = self.state.start_ps + round(
            data_columns * (self.state.cycles_per_char - new_cycles_per_char) * self.state.ps_per_cycle
        )
        new_state = self.state.copy_with(start_ps=new_start_ps, cycles_per_char=new_cycles_per_char)
        return self.update_state(new_state)

    def zoom_in(self, column: int) -> bool:
        """
        Zoom the UI out so the same graphical area covers 1/2 as much time as
        before, centered around column `column`, returning True if the zoom
        level changed, or False if it was unable to.
        """
        return self._zoom_around(
            new_cycles_per_char=max(Top._min_cycles_per_char, self.state.cycles_per_char / 2), column=column
        )

    def zoom_out(self, column: int) -> bool:
        """
        Zoom the UI out so the same graphical area covers 2x as much time,
        centered around column `column`, returning True if the zoom changed, or
        False if it was unable to.
        """
        return self._zoom_around(
            new_cycles_per_char=min(Top._max_cycles_per_char, self.state.cycles_per_char * 2), column=column
        )

    def _translate(self, ps: int, limit: int | None = None) -> bool:
        limit = limit or abs(ps)
        viewable_ps = round(
            self.state.ps_per_cycle * self.state.cycles_per_char * (self.cached_maxcol - self.state.column_header_width)
        )
        absolute_ps = self.state.start_ps + ps
        if ps > 0:
            right_extent = int(self.stream_data.last_time - viewable_ps + limit)
            left_right_extent = self.stream_data.first_time
            absolute_ps = min(max([left_right_extent, self.state.start_ps, right_extent]), absolute_ps)
        else:
            left_extent = self.stream_data.first_time - limit
            right_left_extent = self.stream_data.first_time - (viewable_ps - self.stream_data.last_time)
            absolute_ps = max(min([self.state.start_ps, left_extent, right_left_extent]), absolute_ps)

        new_state = self.state.copy_with(start_ps=absolute_ps)
        return self.update_state(new_state)

    @property
    def _quarter_screen(self) -> int:
        return int(self.state.cycles_per_char * self.state.ps_per_cycle * self.cached_maxcol / 4)

    def scroll_left(self) -> bool:
        return self._translate(-self._quarter_screen)

    def scroll_right(self) -> bool:
        return self._translate(self._quarter_screen)

    def make_selection(self, selection: Event | Selection, views: list[str] | None = None) -> bool:
        if isinstance(selection, Event):
            data_view = self._get_data_view_from_views(views)
            event = selection
            selection = Selection(
                event_row=data_view.key_of(event),
                event=event,
                duration=self.state.ps_per_cycle,
                within_transaction=isinstance(data_view, EventStreamDataTransactionView),
            )

        new_state = self.state.copy_with(selection=selection)

        # Notify the resource view about the selected event in case it wasn't
        # the originator, so it can update its view accordingly
        self.view_rows.update_selected_row(selection, views=views)

        # We must update the state here so that the 'usable_width' property
        # used below is correct
        if not self.update_state(new_state):
            return False

        # If the selected event falls outside the viewable window, shift the
        # window so it remains viewable
        if self.state.selection.is_event():
            ps_per_char = round(new_state.cycles_per_char * new_state.ps_per_cycle)
            max_start_time = selection.start_ps - 2 * ps_per_char
            min_end_time = selection.end_ps + ps_per_char
            end_ps = self.state.start_ps + round(
                self.state.ps_per_cycle * self.state.cycles_per_char * self.usable_width
            )
            if self.state.start_ps > max_start_time:
                new_state = new_state.copy_with(start_ps=max_start_time)
            elif end_ps < min_end_time:
                new_state = new_state.copy_with(start_ps=self.state.start_ps + min_end_time - end_ps)
        return self.update_state(new_state)

    def extend_selection(self, new_selection: Event | Selection) -> bool:
        if isinstance(new_selection, Event):
            data_view = self._get_data_view_from_views()
            event = new_selection
            new_selection = Selection(
                event_row=data_view.key_of(event),
                event=event,
                duration=self.state.ps_per_cycle,
                within_transaction=isinstance(data_view, EventStreamDataTransactionView),
            )

        new_state = self.state.copy_with(selection=self.state.selection.extend(new_selection))
        return self.update_state(new_state)

    def clear_selection(self) -> bool:
        self.frame.focus_position = "body"
        new_state = self.state.copy_with(selection=Selection())
        return self.update_state(new_state)

    def translate_event(self, x_chars: int) -> bool:
        if x_chars == 0:
            return True

        return self._translate(
            int(x_chars * self.state.ps_per_cycle * self.state.cycles_per_char),
            limit=self._quarter_screen,
        )

    def highlight_transactions(self, txids: Iterable[int]) -> bool:
        color_use = dict.fromkeys(range(NUM_EVENT_COLORS), 0)
        for color in self.state.highlighted_transactions.values():
            color_use[color] += 1
        least_used_color = min(color_use, key=color_use.get)

        new_highlighted_transactions = self.state.highlighted_transactions
        for txid in txids:
            new_highlighted_transactions = new_highlighted_transactions.copy_with(txid, least_used_color)

        new_state = self.state.copy_with(highlighted_transactions=new_highlighted_transactions)
        return self.update_state(new_state)

    def unhighlight_transactions(self, txids: Iterable[int]) -> bool:
        new_highlighted_transactions = self.state.highlighted_transactions
        for txid in txids:
            new_highlighted_transactions = new_highlighted_transactions.copy_without(txid)

        new_state = self.state.copy_with(highlighted_transactions=new_highlighted_transactions)
        return self.update_state(new_state)

    def toggle_highlight(self, event: Event) -> bool:
        assert self.state.selection.is_event()
        if "txid" not in self.state.selection.event.data:
            return False

        txid = self.state.selection.event.data["txid"]
        if txid in self.state.highlighted_transactions:
            return self.unhighlight_transactions([txid])
        return self.highlight_transactions([txid])

    def toggle_highlight_all(self, event: Event) -> bool:
        assert self.state.selection.is_event()
        if "txid" not in self.state.selection.event.data:
            return False

        txid = self.state.selection.event.data["txid"]
        ancestors = [a.txid for a in self.stream_data.ancestors(txid)]
        descendants = [a.txid for a in self.stream_data.descendants(txid)]
        all_relatives = set(ancestors + descendants + [txid])
        if len(all_relatives) > Top.MAX_SIMULTANEOUS_TXID_HIGHLIGHTS:
            return self.add_message(
                f"Too many ({len(all_relatives)}) transactions to highlight all of them, try highlighting individual transactions"
            )

        if all_relatives.issubset(self.state.highlighted_transactions.keys()):
            return self.unhighlight_transactions(all_relatives)
        return self.highlight_transactions(all_relatives)

    def mark_event(self, event: Event, mark: str) -> bool:
        new_state = self.state.copy_with(marked_events=self.state.marked_events.copy_with(mark, event))
        return self.update_state(new_state)

    def go_to_marked_event(self, mark: str) -> bool:
        if mark not in self.state.marked_events:
            return False

        event = self.state.marked_events[mark]
        self.make_selection(event)
        self.go_to_time(event.time, max_zoom=Fraction(1, 1))
        return True

    def go_to_time(self, time: int, max_zoom: Fraction | None = None) -> bool:
        new_zoom = min(self.state.cycles_per_char, max_zoom) if max_zoom else self.state.cycles_per_char
        left_padding_ps = round(((self.usable_width * new_zoom - 1) * self.state.ps_per_cycle) / 2)

        new_state = self.state.copy_with(start_ps=time - left_padding_ps, cycles_per_char=new_zoom)
        return self.update_state(new_state)

    def go_to_time_range(self, start_time: int, end_time: int) -> bool:
        """
        Pan and zoom so that the viewport contains the entirety of the
        specified time range.
        """
        ps_range = end_time - start_time
        highest_containing_zoom = Top._min_cycles_per_char
        while highest_containing_zoom < Top._max_cycles_per_char:
            ps_at_zoom = self.usable_width * highest_containing_zoom * self.state.ps_per_cycle
            if ps_at_zoom > ps_range:
                break
            highest_containing_zoom *= 2
        left_padding_ps = round((self.usable_width * highest_containing_zoom * self.state.ps_per_cycle - ps_range) / 2)

        new_state = self.state.copy_with(start_ps=start_time - left_padding_ps, cycles_per_char=highest_containing_zoom)
        return self.update_state(new_state)

    def go_to_transactions(self, txids: Iterable[int]) -> bool:
        transactions = [self.stream_data.transactions[txid] for txid in txids]
        start_time = min([t.start_time for t in transactions])
        end_time = max([t.end_time for t in transactions])
        return self.go_to_time_range(start_time, end_time)

    def search_command(
        self,
        search_string: str,
        rows: list[str] | None = None,
        search_fields: list[str] | None = None,
        search_mask: int | None = None,
        case_sensitive: bool | None = None,
        match_type: MatchType = MatchType.AUTO,
        min_per_cycle: int | None = None,
        views: list[str] | None = None,
    ) -> bool:
        search_term = search_string.strip()
        search_rows = []
        case_sensitive_specified = case_sensitive is not None
        case_sensitive = False if case_sensitive is None else case_sensitive

        rows = rows or []
        for row in rows:
            if row.lower() in ("current", "focused"):
                focused_rows = self.view_rows.focused_event_rows()
                if not focused_rows:
                    return self.add_message("search: no current/focused event rows to search")
                search_rows += focused_rows
            else:
                row_pattern = glob_to_pattern(row)
                row_re = re.compile(f"^{row_pattern}$")
                matching_rows = [row.name for row in self.stream_data if row_re.match(row.name)]
                if not matching_rows:
                    return self.add_message(f"search: failed to find any matching rows for qualifier: {row}")
                search_rows += matching_rows

        if search_mask and match_type not in (MatchType.AUTO, MatchType.INT):
            return self.add_message("search: cannot apply integer search mask when searching for a string")

        try:
            search_term = int(search_term, base=0)
        except ValueError:
            if search_mask or match_type == MatchType.INT:
                return self.add_message(f"search: failed to parse search term as integer: {search_term}")

        if not search_rows:
            search_rows = None
        if not search_fields:
            search_fields = None

        if not case_sensitive_specified and isinstance(search_term, str):
            # If the user has not specified case-sensitivity, use
            # case-sensitive search only when their search string does not
            # contain upper-case letters
            case_sensitive = search_term.lower() != search_term

        return self.search(
            search_term,
            search_rows,
            search_fields,
            search_mask,
            match_type,
            case_sensitive,
            min_per_cycle,
            views=views,
        )

    def search(
        self,
        search_term: int | str | None,
        search_rows: Iterable[str] | None = None,
        search_fields: Iterable[str] | None = None,
        search_mask: int | None = None,
        match_type: MatchType = MatchType.AUTO,
        case_sensitive: bool = False,
        min_per_cycle: int | None = None,
        views: list[str] | None = None,
    ) -> bool:
        views = views or self.view_rows.focused_views()
        data_view = self._get_data_view_from_views(views)

        self.frame.focus_position = "body"

        # If the user searched for the empty string, clear the search
        if isinstance(search_term, str) and len(search_term.strip()) == 0:
            search_term = None
        elif match_type in (MatchType.AUTO, MatchType.INT) and isinstance(search_term, str):
            # Try to convert to an integer if possible
            try:
                search_term = int(search_term, base=0)
            except ValueError:
                if search_mask or match_type == MatchType.INT:
                    return self.add_message(f"search: failed to parse search term as integer: {search_term}")
        elif match_type == MatchType.STRING and not isinstance(search_term, str):
            search_term = str(search_term)

        # Convert the globbed string fields into an iterable containing the
        # 'real' raw field names to search
        if search_fields:
            new_search_fields = FilteringSearcher.get_search_fields(data_view, search_fields)
            if not new_search_fields:
                self.add_message(f"search: No valid fields found matching field specification {search_fields}")
                return True
            search_fields = new_search_fields

        if search_term is not None:
            if self.state.selection:
                starting_point = (
                    self.state.selection.event if self.state.selection.is_event() else self.state.selection.start_ps
                )
            else:
                starting_point = self.state.start_ps

            if isinstance(search_term, int):
                searcher = IntSearcher(search_term, search_rows, search_fields, search_mask)
            else:
                searcher = TextSearcher(search_term, search_rows, search_fields, case_sensitive, self.hexargs_re)

            if min_per_cycle is not None:
                searcher = PerPeriodSearcher(min_per_cycle, searcher)

            self.search_tracker = EventStreamDataSearch(data_view, searcher, starting_point, views=views)

            first_match = self.search_tracker.search_cursor
            if first_match is not None:
                self.make_selection(first_match, views=self.search_tracker.views)
        else:
            searcher = None
            if hasattr(self, "search_tracker"):
                del self.search_tracker

        new_state = self.state.copy_with(searcher=searcher)

        return self.update_state(new_state)

    def search_next(self) -> bool:
        if self.state.searcher is None:
            return False

        next_search_result = self.search_tracker.next()
        if next_search_result is not None:
            return self.make_selection(next_search_result, views=self.search_tracker.views)
        return False

    def search_prev(self) -> bool:
        if self.state.searcher is None:
            return False

        prev_search_result = self.search_tracker.prev()
        if prev_search_result is not None:
            return self.make_selection(prev_search_result, views=self.search_tracker.views)
        return False

    def summarize(
        self,
        start_time: int | None = None,
        end_time: int | None = None,
        rows: Sequence[str | int] | None = None,
        fields: Sequence[str] | None = None,
    ):
        data_view = self._focused_data_view
        if not rows:
            rows = data_view.keys()
        if not start_time:
            start_time = self.stream_data.first_time
        if not end_time:
            end_time = self.stream_data.last_time

        header, contents, footer = generate_summary_table(data_view, start_time, end_time, rows, fields)

        # Generate the title for the table
        picosecond_span = end_time - start_time
        cycle_span = picosecond_span // self.state.ps_per_cycle
        row_text = rows[0] if len(rows) == 1 else f"{len(rows)} rows"
        title = f"Summary of {row_text} over {cycle_span} cycles ({picosecond_span} ps):"

        table = TextTable(header, contents, footer, title)
        table_lines, _ = table._render_table(60)
        for row in table_lines:
            self.add_message(f"  {row.decode()}")

    def start_commit_sync(self) -> None:
        self.saved_stream_data = self.stream_data

        pushout_events = self.commit_syncer.generate_commit_pushout_events(
            self.stream_data.event_rows[self.commit_sync_event],
            self.commit_sync_data_name,
            next_event_id=self.stream_data.max_event_id + 1,
            ps_per_cycle=self.state.ps_per_cycle,
        )

        new_stream_data = self.stream_data.copy_with_events(
            pushout_events,
            insert_after=self.commit_sync_event,
        )

        self.update_stream_data(
            new_stream_data,
            external_column_width=self.commit_syncer.other_column_header_width,
            zoom_to_extents=False,
        )

        self.eval_commands(self.init_sync_commands)

        # Force redrawing the screen since we're updating the state outside
        # of urwid's normal event loop
        self.main_loop.draw_screen()

    def stop_commit_sync(self) -> None:
        # Restore the copy of the event stream data that we took prior to
        # starting instruction sync
        if hasattr(self, "saved_stream_data"):
            self.update_stream_data(self.saved_stream_data, zoom_to_extents=False)
            del self.saved_stream_data

        # Force redrawing the screen since we're updating the state outside
        # of urwid's normal event loop
        self.main_loop.draw_screen()

    def send_commit_sync(self) -> None:
        if not self.commit_syncer or not self.commit_syncer.syncing:
            return

        if self.commit_sync_event not in self.stream_data.event_rows:
            logging.info("Did not send sync because instruction commit row didn't exist")
            return

        closest_instruction_commit = self.stream_data.event_rows[self.commit_sync_event].closest_to(self.state.start_ps)
        if not closest_instruction_commit:
            logging.info("Did not send sync because closest instruction not found to start_ps")
            return

        # If the commit we initially chose to synchronize on isn't present in
        # the other event stream, try a few subsequent commits in case we can
        # find one that is
        other_pushout_index = self.commit_syncer.other_pushout_index
        closest_inum = closest_instruction_commit.data[self.commit_sync_data_name]
        if closest_inum not in other_pushout_index or closest_inum not in self.pushout_index:
            for _ in range(20):
                if next_commit := self.stream_data.event_rows[self.commit_sync_event].oldest_younger(
                    closest_instruction_commit
                ):
                    closest_instruction_commit = next_commit
                    closest_inum = closest_instruction_commit.data[self.commit_sync_data_name]
                    if closest_inum in other_pushout_index and closest_inum in self.pushout_index:
                        break
                else:
                    break

        offset_chars = round(
            (self.state.start_ps - closest_instruction_commit.time)
            * self.state.cycles_per_char.denominator
            / self.state.cycles_per_char.numerator
            / self.state.ps_per_cycle
        )
        sync_state = CommitSyncState(
            inum=closest_instruction_commit.data[self.commit_sync_data_name],
            cycles_per_char=self.state.cycles_per_char,
            expand_rows=self.state.expand_rows,
            chars_rel_to_start=offset_chars,
        )
        self.commit_syncer.send(sync_state)

    def receive_commit_sync(self, sync_state: CommitSyncState):
        if sync_state.inum not in self.commit_sync_index:
            logging.info(f"Received inum not in inum index: {sync_state.inum}")
            return

        # Calculate the time at the left-hand side of the screen based on our
        # scaling of the received offset from the reference inum
        inum_commit_ps = self.commit_sync_index[sync_state.inum]
        new_start_ps = round(
            inum_commit_ps + sync_state.chars_rel_to_start * sync_state.cycles_per_char * self.state.ps_per_cycle
        )

        self.update_state(
            self.state.copy_with(
                cycles_per_char=sync_state.cycles_per_char, expand_rows=sync_state.expand_rows, start_ps=new_start_ps
            ),
            external_sync=True,
        )

    def matching_rows(self, pattern: str) -> list[str]:
        row_pattern = glob_to_pattern(pattern)
        try:
            row_re = re.compile(f"^{row_pattern}$")
        except re.error as e:
            self.add_message(
                f"Saw error `{e}` while attempting to compile pattern `{pattern}` as regular expression with string `{row_pattern}`"
            )
            return []

        data_view = self._focused_data_view
        return [row.name for row in data_view if row_re.match(row.name)]

    def next_matching_row(self, pattern: str) -> str | None:
        matching_rows = self.matching_rows(pattern)
        if not matching_rows:
            return None

        data_view = self._focused_data_view
        indices = [data_view.keys().index(row) for row in matching_rows]
        next_row_index = self.next_row_id()
        sorted_indices = sorted(indices, key=lambda i: i if i >= next_row_index else i + len(data_view.keys()))
        return data_view.keys()[sorted_indices[0]]

    def go_to_row(self, pattern: str) -> bool:
        """
        Try to focus the next row found to match a given 'glob', if such a
        match is found.
        """
        new_row_name = self.next_matching_row(pattern)
        if not new_row_name:
            return False

        self.view_rows.update_selected_row(Selection(new_row_name))

        return True

    def exit(self):
        if self.commit_syncer:
            self.commit_syncer.stop()
        raise urwid.ExitMainLoop()

    def command(self, command_and_args: str) -> bool:
        self.frame.focus_position = "body"

        try:
            command, posargs, kwargs = parse_command_args(command_and_args)
        except ValueError as e:
            self.add_message(str(e))
            return False

        if not command:
            return False

        if command in ("q", "Q", Commands.QUIT, "exit"):
            self.exit()

        cmd = command_definitions.get(command)
        if cmd is not None:
            try:
                posargs, args = cmd.parse_arguments(posargs, kwargs)
                cmd.verify(args, len(posargs))
            except ValueError as e:
                self.add_message(f"{command}: {e}")
                return False
        else:
            args = posargs

        if command == Commands.HELP:
            if "command" in args:
                help_command = args["command"]
                if help_command in command_definitions:
                    self.show_help(help_command)
                else:
                    self.add_message(
                        f"No '{help_command}' command to show help for, use :{command} without arguments to show all commands"
                    )
            else:
                self.show_help()
            return False
        if self.state.loading:
            self.add_message(f"'{command}' cannot be executed while loading")
            return False

        logging.debug("command: %s", command_and_args)

        # The following commands require an event stream to be loaded to work
        # properly, so they are protected by the above check to ensure the
        # event stream is already loaded
        if command == Commands.HELP:
            if "command" in args:
                help_command = args["command"]
                if help_command in command_definitions:
                    self.show_help(help_command)
                else:
                    self.add_message(f"No '{command}' to show help for")
            else:
                self.show_help()
            return False
        if command == Commands.SEARCH:
            if not args:
                self.search(None)
            else:
                self.search_command(
                    args["search_string"],
                    rows=args.get("rows"),
                    search_fields=args.get("fields"),
                    search_mask=args.get("mask"),
                    case_sensitive=args.get("match_case"),
                    match_type=args.get("type"),
                    min_per_cycle=args.get("min_per_cycle"),
                )
        elif command == Commands.SEARCH_ROW:
            focused_rows = self.view_rows.focused_event_rows()
            if not focused_rows:
                self.add_message(f"'{command}': no current/focused event rows to search")
                return False
            self.search(args["search_string"], search_rows=focused_rows)
        elif command == Commands.SEARCH_FIELD:
            self.search(args["search_string"], search_fields=[args["field_glob"]])
        elif command == Commands.SEARCH_INT:
            self.search(args["search_integer"], search_mask=args.get("integer_mask"))
            return False
        elif command == Commands.PIN_ROW:
            if len(args) == 1:
                rows_to_pin = self.matching_rows(args["row_glob"])
                if not rows_to_pin:
                    self.add_message(f"'{command}' accepts at most one argument: the row name/pattern to pin")
                    return False
                for row in rows_to_pin:
                    self._pinned_resource_view.add_event(row)
                self._update_view_rows()
            elif len(args) == 0:
                row_type, event = self._resource_view.focused_row()
                if row_type == RowType.EVENT:
                    self._pinned_resource_view.add_event(event)
                    self._update_view_rows()
                else:
                    self.add_message("Can only pin event rows")
                    return False
        elif command == Commands.UNPIN_ROW:
            if len(args) == 1 and "all" in args:
                self._pinned_resource_view.remove_all_events()
            elif len(args) == 1:
                rows_to_unpin = self.matching_rows(args["row_glob"])
                if not rows_to_unpin:
                    self.add_message(f"'{command}' accepts at most one argument: the row name/pattern to unpin")
                    return False
                for row in rows_to_unpin:
                    self._pinned_resource_view.remove_event(row)
            elif len(args) == 0:
                self._pinned_resource_view.remove_focused_event()
            self._update_view_rows()
        elif command == Commands.CLEAR:
            if len(args) > 0:
                clear_search = "search" in args
                clear_highlights = "highlights" in args
            else:
                clear_search = True
                clear_highlights = True

            new_searcher = self.state.searcher
            if clear_search and self.state.searcher is not None:
                new_searcher = None
                del self.search_tracker

            new_highlights = HashableFrozenDict() if clear_highlights else self.state.highlighted_transactions

            new_state = self.state.copy_with(
                searcher=new_searcher,
                highlighted_transactions=new_highlights,
            )
            self.update_state(new_state)
        elif command == Commands.SUMMARIZE:
            focused_rows = self.view_rows.focused_event_rows()
            if not focused_rows:
                self.add_message(f"'{command}': no current/focused event rows to summarize")
                return False

            fields = args.get("fields")
            if fields:
                fields = FilteringSearcher.get_search_fields(self._focused_data_view, fields)

            if len(args) == 2:
                marks = [args["starting_mark"], args["ending_mark"]]
                for mark in marks:
                    if mark not in self.state.marked_events:
                        self.add_message(f"Unable to summarize as requested because mark '{mark}' does not exist.")
                        return False
                times = sorted([self.state.marked_events[m].time for m in marks])
                self.summarize(start_time=times[0], end_time=times[1], rows=focused_rows, fields=fields)
            else:
                self.summarize(rows=focused_rows, fields=fields)
        elif command == Commands.SYNC_COMMITS:
            if len(args) != 1:
                self.add_message(
                    f"'{command}' requires you to pass a path to use as the base name for the FIFOs used to synchronize with the other catscan process"
                )
                return False
            if self.commit_sync_event is None or self.commit_sync_data_name is None:
                self.add_message("No sync event and/or data-name specified")
                return False
            if "stop" in args:
                self.commit_syncer.stop()
                self.commit_syncer = None
            else:
                self.commit_syncer = CommitSyncer(
                    args["fifo_basename"],
                    self.start_commit_sync,
                    self.stop_commit_sync,
                    self.receive_commit_sync,
                    self.state.column_header_width,
                    self.pushout_index,
                )

                self.commit_syncer.start(self.main_loop)
        elif command == Commands.ZOOM:
            level_or_type = args["level_or_type"]
            if level_or_type == ZoomTypes.EXTENTS:
                self.go_to_time_range(self.stream_data.first_time, self.stream_data.last_time)
            elif level_or_type == ZoomTypes.FIT:
                time_range = self._focused_view.focused_time_range()
                if not all(t == 0 for t in time_range):
                    self.go_to_time_range(*time_range)
            elif level_or_type == ZoomTypes.SEARCH:
                if self.state.searcher is None:
                    self.add_message(f"'{command}' requires a search when zooming to search")
                    return False

                self.go_to_time_range(self.search_tracker.start_time, self.search_tracker.end_time)
            elif level_or_type == ZoomTypes.HIGHLIGHTS:
                if not self.state.highlighted_transactions:
                    self.add_message(f"'{command}' requires a highlights when zooming to highlights")
                    return False

                start_time = maxsize
                end_time = 0
                for txid in self.state.highlighted_transactions:
                    start_time = min(self._focused_data_view.transactions[txid].start_time, start_time)
                    end_time = max(self._focused_data_view.transactions[txid].end_time, end_time)

                if end_time < start_time:
                    self.add_message(f"'{command}' could not zoom, invalid highlight zoom range")
                    return False

                self.go_to_time_range(start_time, end_time)
            elif level_or_type == ZoomTypes.MARKS:
                marks = args.get("marks", self.state.marked_events.keys())
                if not marks:
                    self.add_message(f"'{command}' cannot zoom to marks if no marks are set")
                    return False
                for mark in marks:
                    if mark not in self.state.marked_events:
                        self.add_message(f"Unable to zoom because mark '{mark}' does not exist.")
                        return False

                times = sorted([self.state.marked_events[m].time for m in marks])
                self.go_to_time_range(times[0], times[-1])
            else:
                if "characters" in args:
                    level_or_type = 1 / level_or_type
                self._zoom_around(level_or_type, self._focused_view.center_column())
        elif command == Commands.MARKS:
            message = "Marks:\n"
            for mark, event in self.state.marked_events.items():
                cycles = round(event.time / self.state.ps_per_cycle)
                message += f"\n {mark} : {event.abbrev} @ {cycles} cycles ({event.name})"
            self.add_message(message)
        else:
            # If the command didn't match an actual command, try to navigate to
            # it as a time/cycle or row name
            is_time, parts = command_definitions[DefaultCommands.GOTO_TIME].matches(command)
            if is_time:
                (number, unit) = parts
                if unit == "ps":
                    multiplier = 1
                elif unit == "ns":
                    multiplier = 1e3
                elif unit == "us":
                    multiplier = 1e6
                elif unit == "ms":
                    multiplier = 1e9
                elif unit == "s":
                    multiplier = 1e12
                else:
                    multiplier = self.state.ps_per_cycle

                time = int(float(number) * multiplier)
                self.go_to_time(time)
            else:
                found_row = self.go_to_row(command)
                if not found_row:
                    self.add_message(f"Error: unknown command `{command}`, type `:help` to see available commands.")
                    return False

        logging.debug("command success: %s", command_and_args)

        return True

    def eval_commands(self, commands: list[str]) -> bool:
        for cmd in commands:
            if not self.command(cmd):
                self.add_message(
                    "Failed to execute any remaining `--eval` commands after one command failed to execute"
                )
                return False
        return True

    def mouse_event(
        self,
        size: tuple[()] | tuple[int] | tuple[int, int],
        event: str,
        button: int,
        col: int,
        row: int,
        focus: bool,
    ) -> bool | None:
        keyless_event = re.sub(r"^.*?mouse", "mouse", event)
        original_event = event
        if keyless_event == "mouse press":
            # Track last mouse press as release does not always have a button
            self.last_mouse_press_button = button
        elif keyless_event == "mouse drag":
            # Reset last mouse press on drag so it's no longer considered a "click"
            self.last_mouse_press_button = 0
        elif keyless_event == "mouse release" and (self.last_mouse_press_button == 0 or button == 0):
            button = self.last_mouse_press_button

        current_time = self.main_loop.event_loop._loop.time()
        if (
            keyless_event == "mouse release"
            and (event, button) == self.last_mouse_release_button
            and self.last_mouse_release_time is not None
            and (current_time - self.last_mouse_release_time) <= DOUBLE_CLICK_TIMEOUT
        ):
            event = event.replace("release", "double_release")

        if keyless_event == "mouse release":
            self.last_mouse_release_time = current_time
            self.last_mouse_release_button = (original_event, button)

        handled = False
        if self.state.show_help:
            help_coords = self.help.coords(size)
            if help_coords.is_within(col, row):
                handled = self.help.mouse_event(
                    help_coords.size, event, button, col - help_coords.left, row - help_coords.top, focus
                )
        elif len(self.state.messages) > 0:
            message_coords = self.messages.coords(size)
            if message_coords.is_within(col, row):
                handled = self.messages.mouse_event(
                    message_coords.size, event, button, col - message_coords.left, row - message_coords.top, focus
                )
        else:
            handled = self.frame.mouse_event(size, event, button, col, row, focus)

        if handled:
            self._invalidate()
        return handled

    def handle_keypress(
        self,
        size: tuple[()] | tuple[int] | tuple[int, int],
        key: str,
    ) -> str | None:
        if key == "focus in":
            self.update_state(self.state.copy_with(has_focus=True))
            key = None
        elif key == "focus out":
            self.update_state(self.state.copy_with(has_focus=False))
            key = None
        elif self.marking:
            self.marking = False
            if self.state.selection.is_event():
                self.mark_event(self.state.selection.event, key)
                key = None
        elif self.going_to_mark:
            self.going_to_mark = False
            if self.go_to_marked_event(key):
                key = None
        elif self.state.show_help:
            help_coords = self.help.coords(size)
            key = self.help.keypress((help_coords.width, help_coords.height), key)
        elif len(self.state.messages) > 0:
            message_coords = self.messages.coords(size)
            key = self.messages.keypress((message_coords.width, message_coords.height), key)
        elif self.state.loading:
            key = self.loading_screen.keypress(size, key)

        if key is None:
            self._invalidate()
            return None

        ret = self.frame.keypress(size, key)

        if ret in action_keypresses[ACTIONS.SEARCH]:
            self.status_bar.enter_search()
            self.frame.focus_position = "footer"
            ret = None
        elif ret in action_keypresses[ACTIONS.COMMAND]:
            self.status_bar.enter_command()
            self.frame.focus_position = "footer"
            ret = None
        elif self.frame.focus_position == "footer" and ret in action_keypresses[ACTIONS.STATUS_BAR_CANCEL]:
            self.frame.focus_position = "body"
            if self.status_bar.cancel_operation():
                ret = None
        elif self.state.selection and ret in action_keypresses[ACTIONS.EVENT_DETAIL_CLOSE]:
            if self.clear_selection():
                ret = None
        elif ret in action_keypresses[ACTIONS.SEARCH_NEXT]:
            if self.search_next():
                ret = None
        elif ret in action_keypresses[ACTIONS.SEARCH_PREV]:
            if self.search_prev():
                ret = None
        elif ret in action_keypresses[ACTIONS.HELP]:
            if self.show_help():
                ret = None
        elif self.state.selection.is_event() and ret in action_keypresses[ACTIONS.TOGGLE_TXN_HIGHLIGHT]:
            if self.toggle_highlight(self.state.selection.event):
                ret = None
        elif self.state.selection.is_event() and ret in action_keypresses[ACTIONS.TOGGLE_TXN_HIGHLIGHT_ALL]:
            if self.toggle_highlight_all(self.state.selection.event):
                ret = None
        elif self.state.selection.is_event() and ret in action_keypresses[ACTIONS.MARK_EVENT]:
            self.marking = True
            ret = None
        elif ret in action_keypresses[ACTIONS.GO_TO_MARKED_EVENT]:
            self.going_to_mark = True
            ret = None
        elif ret in action_keypresses[ACTIONS.GO_TO_FOCUSED_FIRST_EVENT]:
            time_range = self._focused_view.focused_time_range()
            self.go_to_time(time_range[0])
            ret = None
        elif ret in action_keypresses[ACTIONS.GO_TO_FOCUSED_LAST_EVENT]:
            time_range = self._focused_view.focused_time_range()
            self.go_to_time(time_range[1])
            ret = None
        elif ret in action_keypresses[ACTIONS.ZOOM_FIT_FOCUSED]:
            time_range = self._focused_view.focused_time_range()
            if not all(t == 0 for t in time_range):
                self.go_to_time_range(*time_range)
            ret = None
        elif ret in action_keypresses[ACTIONS.QUIT]:
            self.exit()

        if ret is None:
            self._invalidate()

        return ret

    def clear_unhandled_keypresses(self) -> None:
        if self.unhandled_keys_timer:
            self.main_loop.remove_alarm(self.unhandled_keys_timer)
            self.unhandled_keys_timer = None

        self.recent_unhandled_keys.clear()

    def record_unhandled_keypress(self, key: str) -> str | None:
        """
        If this single keypress wasn't handled, record it on a 'stack' of
        recent unhandled keypresses, and clear that stack whenever a key hasn't
        been pressed in KEYPRESS_COMBINATION_TIMEOUT seconds.
        """

        if self.unhandled_keys_timer:
            self.main_loop.remove_alarm(self.unhandled_keys_timer)

        self.recent_unhandled_keys.append(key)

        self.unhandled_keys_timer = self.main_loop.set_alarm_in(
            KEYPRESS_COMBINATION_TIMEOUT,
            lambda _loop, _arg: self.clear_unhandled_keypresses(),
        )

    def keypress(
        self,
        size: tuple[()] | tuple[int] | tuple[int, int],
        key: str,
    ) -> str | None:
        # make a copy because the stack of recent unhandled keys can be
        # modified in a background thread while we're using it
        recent_keys = self.recent_unhandled_keys.copy()

        ret = key
        for num_recent_keys in range(len(recent_keys) + 1):
            keys = "-".join(recent_keys[-num_recent_keys:] + [key]) if num_recent_keys else key
            if not self.handle_keypress(size, keys):
                ret = None
                break

        # Clear list of unhandled keypresses if something handled a keypress.
        # Add this unhandled keypress to the stack if not.
        if ret is None:
            self.clear_unhandled_keypresses()
        else:
            self.record_unhandled_keypress(ret)

        return ret
