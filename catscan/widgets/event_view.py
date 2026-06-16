# Copyright (c) 2024 Ampere Computing. All rights reserved.
# SPDX-License-Identifier: BSD-3-Clause

import logging
import math
from collections.abc import Callable, Iterable, Iterator
from typing import Any

import urwid
from perf_streams.event_stream import Event

from catscan.data import EventStreamData, EventStreamDataEventView, EventStreamDataView
from catscan.state import CatscanState, Selection
from catscan.user_input import ACTIONS, action_keypresses, action_mouseevents
from catscan.widgets.event_row import EventRow, EventRowBase, RowType
from catscan.widgets.scrollbar import FixedWidthScrollBar
from catscan.widgets.separators import HorizontalBorder, MiddleBorder


class View:
    """Abstract concept of a view."""

    def __init__(self, name: str, state: CatscanState, stream_data: EventStreamData):
        self.name = name
        self.state = state
        self.stream_data = stream_data
        self.parent_view = None

    def data_view(self, **kwargs: Any) -> EventStreamData:
        return self.stream_data

    def update_stream_data(self, stream_data: EventStreamData) -> None:
        self.stream_data = stream_data

    def update_state(self, new_state: CatscanState) -> bool:
        if new_state == self.state:
            return False

        self.state = new_state
        return True

    def _update_selected_row_name(self, newly_selected_row: str | int) -> None:
        raise NotImplementedError

    def update_selected_row(self, selection: Event | Selection, views: list[str] | None = None) -> None:
        if isinstance(selection, Selection):
            self._update_selected_row_name(selection.event_row)
        else:
            self._update_selected_row_name(selection.name)

    def update_focus(self, name: str | None = None):
        name = name or self.name
        if self.parent_view is not None and name is not None:
            self.parent_view.update_focus(name)

    def has_focus(self) -> bool:
        raise NotImplementedError

    def focused_row(self) -> tuple[RowType, str | int]:
        raise NotImplementedError

    def focused_rows(self, row_type: RowType, all_views: bool = False) -> list[str | int]:
        rt, r = self.focused_row()
        return [r] if rt is row_type else []

    def focused_event_rows(self, all_views: bool = False) -> list[str | int]:
        return self.focused_rows(RowType.EVENT, all_views=all_views)

    def focused_group_rows(self, all_views: bool = False) -> list[str | int]:
        return self.focused_rows(RowType.GROUP, all_views=all_views)

    def focused_time_range(self, all_views: bool = False) -> tuple[int, int]:
        raise NotImplementedError

    def focused_views(self, all_views: bool = False) -> list[str]:
        return [self.name] if self.has_focus() else []

    def center_column(self, maxcol: int | None = None) -> int:
        raise NotImplementedError

    def max_column_header_width(self) -> int:
        raise NotImplementedError


class Views(View):
    """Multiple views by name.

    Supports hierarchical construction/indexing via '.' as a separator between levels/views.
    """

    def __init__(self, state: CatscanState, *views: View, name: str | None):
        super().__init__(name, state, None)
        self._last_focused_view = None
        self._views: dict[str, EventView] = {}
        for view in views:
            self.add(view)

    def add(self, view: View) -> View:
        if self.name:
            view.name = f"{self.name}.{view.name}"

        view.parent_view = self
        self._views[view.name] = view
        return view

    def __iter__(self) -> Iterator:
        return iter(self._views)

    def __getitem__(self, fullname: str) -> View:
        name = fullname.replace(f"{self.name}.", "") if self.name else fullname
        parts = name.split(".")
        local_name = (f"{self.name}.{parts[0]}" if self.name else parts[0]) if len(parts) > 1 else fullname
        view = self._views[local_name]
        if len(parts) == 1 or not isinstance(view, Views):
            return view

        return view[fullname]

    @property
    def names(self) -> Iterable[str]:
        return self._views.keys()

    @property
    def event_views(self) -> Iterable[View]:
        return self._views.values()

    def items(self):  # noqa: ANN201
        return self._views.items()

    def update_stream_data(self, stream_data: EventStreamData) -> None:
        for view in self.event_views:
            view.update_stream_data(stream_data)

    def update_state(self, new_state: CatscanState) -> bool:
        if not super().update_state(new_state):
            return False

        for view in self.event_views:
            view.update_state(new_state)

        return True

    def update_selected_row(self, selection: Event | Selection, views: list[str] | None = None) -> None:
        if selection.view:
            self[selection.view].update_selected_row(selection, views=views)
        elif views:
            for name in views:
                self[name].update_selected_row(selection, views=views)
        else:
            for view in self.event_views:
                view.update_selected_row(selection, views=views)

    def update_focus(self, name: str | None = None):
        self._last_focused_view = name
        super().update_focus(name)

    def focused_views(self, all_views: bool = False) -> list[str]:
        focused = []
        if all_views:
            for view in self.event_views:
                focused.extend(view.focused_views(all_views=all_views))
        elif self._last_focused_view is not None:
            focused = [self._last_focused_view]

        return focused

    def focused_rows(self, row_type: RowType, all_views: bool = False) -> list[str]:
        focused = []
        if all_views:
            for view in self.event_views:
                focused.extend(view.focused_rows(row_type, all_views=all_views))
        elif self._last_focused_view:
            focused = self[self._last_focused_view].focused_rows(row_type, all_views=all_views)

        return focused

    def focused_time_range(self, all_views: bool = False) -> tuple[int, int]:
        start = None
        end = None
        if all_views:
            for view in self.event_views:
                s, e = view.focused_time_range(all_views=all_views)
                start = s if start is None else min(start, s)
                end = e if end is None else min(end, e)
        elif self._last_focused_view:
            return self[self._last_focused_view].focused_time_range(all_views=all_views)

        return start, end

    def max_column_header_width(self) -> int:
        return max(view.max_column_header_width() for view in self.event_views)

    def view_widgets(self) -> list[View]:
        non_empty_views = []
        for view in self.event_views:
            max_rows = view.max_rows() if hasattr(view, "max_rows") else None
            if max_rows is None:
                non_empty_views.append((view, (urwid.WHSettings.WEIGHT, 1)))
            elif not view.empty():
                non_empty_views.append((view, (urwid.WHSettings.PACK, None)))

        view_rows = []
        for view in non_empty_views:
            if len(view_rows) % 2 == 1:
                view_rows.append((MiddleBorder(self.state), (urwid.WHSettings.PACK, None)))
            view_rows.append(view)

        return view_rows


class LazyEventListWalker(urwid.ListWalker):
    """Lazy construction walker which understands all possible events."""

    def __init__(
        self,
        create_rows: Callable,
        all_rows: Callable,
        has_groups: bool = False,
        load_rows: int = 64,
        max_rows: int = 512,
        cleanup_threshold: float = 0.1,
    ):
        self.focus = 0
        self._rows: dict[int, EventRowBase] = {}
        self._create_rows = create_rows
        self._load_rows = load_rows
        self._max_rows = max_rows
        self._cleanup_above = int(math.ceil(max_rows * (1 + cleanup_threshold)))
        self._total_rows = None
        self._all_rows_func = all_rows
        self._has_groups = has_groups
        super().__init__()

    def set_focus(self, position: int) -> None:
        if not 0 <= position < len(self):
            raise IndexError(f"No widget at position {position}")

        self.focus = position
        self._modified()

    def next_position(self, position: int) -> int:
        if len(self) - 1 <= position:
            raise IndexError
        return position + 1

    def prev_position(self, position: int) -> int:
        if position <= 0:
            raise IndexError
        return position - 1

    def positions(self, reverse: bool = False) -> Iterable[int]:
        if reverse:
            return range(len(self) - 1, -1, -1)
        return range(len(self))

    def update_state(self, new_state: CatscanState) -> bool:
        for row in self._rows.values():
            row.update_state(new_state)

        return True

    def clear(self):
        self._rows = {}
        self._total_rows = None
        self._modified()

    def _populate(self, position: int):
        rows = self._create_rows(position, self._load_rows)
        if not rows:
            raise IndexError

        self._rows.update({row.row_index: row for row in rows})

    def _cleanup(self, position: int):
        to_remove = sorted(self._rows.keys(), key=lambda k: abs(k - position), reverse=True)[
            : (self._cleanup_above - self._max_rows)
        ]
        for key in to_remove:
            del self._rows[key]

    def __getitem__(self, position: int) -> EventRowBase:
        if position >= len(self):
            raise IndexError
        if position not in self._rows:
            self._populate(position)
        if len(self._rows) > self._cleanup_above:
            self._cleanup(position)

        return self._rows[position]

    def __len__(self) -> int:
        if self._total_rows is None:
            expand, all_rows = self._all_rows_func()
            group_rows = len({ed.group for ed in all_rows}) if self._has_groups else 0
            event_rows = sum(max(1, ed.max_events_per_time()) for ed in all_rows) if expand else len(all_rows)
            self._total_rows = event_rows + group_rows

        return self._total_rows


class LazyEventListBox(urwid.ListBox):
    """ListBox for LazyEventListWalker."""

    def __init__(
        self,
        body: urwid.ListWalker | Iterable[urwid.Widget],
        all_rows: Callable,
        has_groups: bool = False,
    ) -> None:
        self._all_rows_func = all_rows
        self._has_groups = has_groups
        self._last_scrollpos = (None, None)
        super().__init__(body)

    def _calculate_scrollpos(self, all_rows: EventStreamDataView, start: int, stop: int) -> int:
        group_rows = 0
        if self._has_groups:
            prev = None
            for ed in all_rows.within_indicies(start, stop):
                if start == 0 if prev is None else prev.group != ed.group:
                    group_rows += 1
                prev = ed

        event_rows = sum(max(1, ed.max_events_per_time()) for ed in all_rows.within_indicies(start, stop))
        return event_rows + group_rows

    def get_scrollpos(self, size: tuple[int, int] | None = None, focus: bool = False) -> int:
        """Current scrolling position."""
        self._check_support_scrolling()

        if not self._body:
            return 0

        if size is not None:
            self._rendered_size = size

        mid, top, _bottom = self.calculate_visible(self._rendered_size, focus)
        start_row = top.trim

        pos = top.fill[-1].position if top.fill else mid.focus_pos

        expand, all_rows = self._all_rows_func()
        if not expand:
            return start_row + pos

        last_pos, last_offset = self._last_scrollpos
        if last_pos is None or (pos < last_pos and abs(pos - last_pos) > pos):
            offset = self._calculate_scrollpos(all_rows, 0, pos + 1)
        elif last_pos < pos:
            offset = last_offset + self._calculate_scrollpos(all_rows, last_pos + 1, pos + 1)
        elif last_pos > pos:
            offset = last_offset - self._calculate_scrollpos(all_rows, pos, last_pos)
        else:
            offset = last_offset

        self._last_scrollpos = (pos, offset)
        return start_row + offset

    def require_relative_scroll(self, size: tuple[int, int], focus: bool = False) -> bool:
        return False

    def rows_max(self, size: tuple[int, int] | None = None, focus: bool = False) -> int:
        """Scrollable protocol for sized iterable and not wrapped around contents."""
        self._check_support_scrolling()

        if size is not None:
            self._rendered_size = size

        return len(self.body)

    @property
    def __len__(self) -> Callable[[], int]:
        return self.rows_max

    @property
    def __length_hint__(self) -> Callable[[], int]:
        return self.rows_max


class EventView(urwid.WidgetWrap, View):
    """
    Display rows of events in a scrollable container. If the view is zoomed out
    far enough or 'condensed', each character may represent multiple cycles or
    multiple events.
    """

    def __init__(
        self,
        name: str,
        state: CatscanState,
        stream_data: EventStreamData,
        on_zoom_in: Callable,
        on_zoom_out: Callable,
        on_scroll_left: Callable,
        on_scroll_right: Callable,
        on_toggle_expanded: Callable,
        on_make_selection: Callable,
        on_extend_selection: Callable,
        on_translate_event: Callable,
        length_hint: int = 1,
    ) -> None:
        self.name = name
        self.state = state

        self.on_zoom_in = on_zoom_in
        self.on_zoom_out = on_zoom_out
        self.on_scroll_left = on_scroll_left
        self.on_scroll_right = on_scroll_right
        self.on_toggle_expanded = on_toggle_expanded
        self._on_make_selection = on_make_selection
        self._on_extend_selection = on_extend_selection
        self.on_translate_event = on_translate_event

        self.list_walker = None
        self.list_box = None
        self._construct_list_box(length_hint)
        self.scrollable = FixedWidthScrollBar(self.list_box, trough_char="│")
        self._last_mouse_location = None
        self._columns = 0

        View.__init__(self, name, state, stream_data)
        self.update_stream_data(stream_data)

        urwid.WidgetWrap.__init__(self, self.scrollable)

    @property
    def has_groups(self) -> bool:
        return False

    def _construct_list_box(self, length_hint: int = 1):
        many_rows = length_hint > 512
        assign_to_scrollbar = self.list_box is not None and (many_rows ^ isinstance(self.list_box, LazyEventListBox))
        if not assign_to_scrollbar and self.list_box is not None:
            return

        if many_rows:
            self.list_walker = LazyEventListWalker(
                create_rows=self._create_rows_around_index,
                all_rows=self._all_rows,
                has_groups=self.has_groups,
            )
            self.list_box = LazyEventListBox(self.list_walker, all_rows=self._all_rows, has_groups=self.has_groups)
        else:
            self.list_walker = urwid.SimpleFocusListWalker([self.create_empty_row()])
            self.list_box = urwid.ListBox(self.list_walker)

        if assign_to_scrollbar:
            self.scrollable._original_widget = self.list_box

    def data_view(self, **kwargs: Any) -> EventStreamDataEventView:
        return self.stream_data.events(**kwargs)

    def iter_event_rows(self, **kwargs: Any) -> EventStreamDataEventView:
        return self.data_view(**kwargs)

    def on_make_selection(self, selection: Any | Selection, *args: Any, **kwargs: Any) -> bool:
        if isinstance(selection, Selection):
            selection.assign_view(self.name)
        return self._on_make_selection(selection, *args, **kwargs)

    def on_extend_selection(self, selection: Any | Selection, *args: Any, **kwargs: Any) -> bool:
        if isinstance(selection, Selection):
            selection.assign_view(self.name)
        return self._on_extend_selection(selection, *args, **kwargs)

    def _all_rows(self) -> tuple[bool, EventStreamDataEventView]:
        return self.state.expand_rows, self.iter_event_rows()

    def total_rows(self) -> int:
        if self._total_rows is None:
            group_rows = len({ed.group for ed in self.iter_event_rows()}) if self.has_groups else 0
            event_rows = len(self.iter_event_rows())
            self._total_rows = group_rows + event_rows

        return self._total_rows

    def _create_rows_around_index(self, index: int, around: int = 0) -> list[EventRowBase]:
        total_rows = self.total_rows()
        if index >= total_rows or index < 0:
            return []

        rows = self.iter_event_rows()
        start = max(0, index - around)
        stop = min(total_rows, index + around + 1)
        return [self.create_row(row, start + offset) for offset, row in enumerate(rows.within_indicies(start, stop))]

    def create_row(self, row: Any, row_index: int, **kwargs: Any):
        raise NotImplementedError

    def create_empty_row(self) -> EventRowBase:
        return EventRowBase()

    def add_rows(self):
        for row_index, row in enumerate(self.iter_event_rows()):
            self.list_walker.append(self.create_row(row, row_index))

    def max_rows(self) -> int | None:
        return None

    def update_rows(self):
        self._total_rows = None
        self._construct_list_box(self.total_rows())
        self.list_walker.clear()
        if not isinstance(self.list_walker, LazyEventListWalker):
            self.add_rows()
            if not self.list_walker:
                self.list_walker.append(self.create_empty_row())

        self._invalidate()

    def update_stream_data(self, stream_data: EventStreamData) -> None:
        super().update_stream_data(stream_data)
        self.update_rows()

    def update_state(self, new_state: CatscanState) -> bool:
        if not super().update_state(new_state):
            return False

        if isinstance(self.list_walker, LazyEventListWalker):
            self.list_walker.update_state(self.state)
        else:
            for row in self.list_walker:
                row.update_state(self.state)

        self._invalidate()
        return True

    def _update_selected_row_name(self, newly_selected_row: str) -> None:
        for row_index, row in enumerate(self.iter_event_rows()):
            if newly_selected_row == row.name:
                self.list_walker.set_focus(row_index)
                return

    def has_focus(self) -> bool:
        return self.list_walker.get_focus()[0] is not None

    def focused_row(self) -> tuple[RowType, str]:
        rowwidget, _ = self.list_walker.get_focus()
        return rowwidget.row_id()

    def focused_time_range(self, all_views: bool = False) -> tuple[int, int]:
        rowwidget, _ = self.list_walker.get_focus()
        if isinstance(rowwidget, EventRow):
            return rowwidget.ed.start_time, rowwidget.ed.end_time
        return 0, 0

    def center_column(self, maxcol: int | None = None) -> int:
        maxcol = maxcol or self._columns
        return self.state.column_header_width + round((maxcol - self.state.column_header_width) / 2)

    def _shift_focus(self, size: tuple[int, int], row_translation: int) -> bool:
        (maxcol, max_inset) = size
        current_inset = self.list_box.get_focus_offset_inset(size)[0]
        inset = current_inset - row_translation
        if inset >= max_inset or inset < 0:
            return False

        self.list_box.shift_focus((maxcol, max_inset), inset)
        return True

    def _shift_view(self, size: tuple[int, int], row_translation: int) -> None:
        last_row = len(self.list_box) - 1
        position = min(
            max(0, self.list_box.focus_position + row_translation),
            last_row,
        )
        coming_from = "above" if row_translation > 0 else "below"
        logging.debug(
            "Shift view to %d / %d from %s",
            position,
            last_row,
            coming_from,
        )
        self.list_box.set_focus(position, coming_from)

    def mouse_event(
        self,
        size: tuple[()] | tuple[int] | tuple[int, int],
        event: str,
        button: int,
        col: int,
        row: int,
        focus: bool,
    ) -> bool | None:
        eb = (event, button)
        mouse_diff = (0, 0)
        if self._last_mouse_location is not None:
            mouse_diff = (col - self._last_mouse_location[0], row - self._last_mouse_location[1])
        self._last_mouse_location = (col, row)

        if eb in action_mouseevents[ACTIONS.ZOOM_IN]:
            if col > self.state.column_header_width:
                self.on_zoom_in(col)
                return True
        elif eb in action_mouseevents[ACTIONS.ZOOM_OUT]:
            if col > self.state.column_header_width:
                self.on_zoom_out(col)
                return True
        elif eb in action_mouseevents[ACTIONS.TRANSLATE_RIGHT]:
            self.on_scroll_right()
            return True
        elif eb in action_mouseevents[ACTIONS.TRANSLATE_LEFT]:
            self.on_scroll_left()
            return True
        elif eb in action_mouseevents[ACTIONS.PAN] and mouse_diff != (0, 0):
            self.on_translate_event(-mouse_diff[0])
            if mouse_diff[1] != 0:
                self._shift_focus(size, -mouse_diff[1])

            return True

        return self._w.mouse_event(size, event, button, col, row, focus)

    def keypress(
        self,
        size: tuple[()] | tuple[int] | tuple[int, int],
        key: str,
    ) -> str | None:
        (maxcol, maxrow) = size

        # Because the underlying container widget only understands 'up' and
        # 'down' translate whatever keys we want to use for down/up (i.e. j/k)
        # into literal down/up
        key_translations = {
            ACTIONS.SCROLL_DOWN: "down",
            ACTIONS.SCROLL_UP: "up",
            ACTIONS.SCROLL_PAGE_DOWN: "page down",
            ACTIONS.SCROLL_PAGE_UP: "page up",
        }
        for action, translation in key_translations.items():
            if key in action_keypresses[action]:
                key = translation
                break

        # First, see if any of the children of this widget want to handle these
        # keypresses
        key = self._w.keypress(size, key)
        if key is None:
            return None

        handled = False
        if key in action_keypresses[ACTIONS.ZOOM_IN]:
            handled = self.on_zoom_in(self.center_column(maxcol))
        elif key in action_keypresses[ACTIONS.ZOOM_OUT]:
            handled = self.on_zoom_out(self.center_column(maxcol))
        elif key in action_keypresses[ACTIONS.TRANSLATE_RIGHT]:
            handled = self.on_scroll_right()
        elif key in action_keypresses[ACTIONS.TRANSLATE_LEFT]:
            handled = self.on_scroll_left()
        elif key in action_keypresses[ACTIONS.RESOURCE_VIEW_TOGGLE_EXPANDED]:
            handled = self.on_toggle_expanded("TODO make this an event row name")
        elif key in action_keypresses[ACTIONS.SCROLL_HALF_PAGE_UP]:
            self._shift_view(size, self.list_box.get_focus_offset_inset(size)[1] - int(maxrow / 2))
            handled = True
        elif key in action_keypresses[ACTIONS.SCROLL_HALF_PAGE_DOWN]:
            self._shift_view(size, self.list_box.get_focus_offset_inset(size)[1] + int(maxrow / 2))
            handled = True
        elif key in action_keypresses[ACTIONS.SCROLL_TOP]:
            self._shift_view(size, -self.list_box.focus_position)
            handled = True
        elif key in action_keypresses[ACTIONS.SCROLL_BOTTOM]:
            self._shift_view(size, len(self.list_box) - 1)
            handled = True
        elif key in action_keypresses[ACTIONS.TOP_FOCUS]:
            self.list_box.set_focus_valign("top")
            handled = True
        elif key in action_keypresses[ACTIONS.CENTER_FOCUS]:
            self.list_box.set_focus_valign("middle")
            handled = True
        elif key in action_keypresses[ACTIONS.BOTTOM_FOCUS]:
            self.list_box.set_focus_valign("bottom")
            handled = True

        if handled:
            return None

        return key

    def render(self, size: tuple[()] | tuple[int] | tuple[int, int], focus: bool = False) -> urwid.Canvas:
        if focus:
            super().update_focus()
        self._columns = size[0]
        return super().render(size, focus=focus)


class RowViews(urwid.WidgetWrap, Views):
    """Views as stacked rows."""

    def __init__(self, state: CatscanState, *views: EventView, name: str | None = None):
        Views.__init__(self, state, *views, name=name)

        self._rows = []
        self._pile = urwid.Pile([])
        self.update_rows()
        urwid.WidgetWrap.__init__(self, self._pile)

    def update_state(self, new_state: CatscanState) -> bool:
        if not super().update_state(new_state):
            return False

        for widget in self._rows:
            if isinstance(widget, HorizontalBorder):
                widget.update_state(new_state)

        return True

    def update_rows(self):
        for view in self.event_views:
            if isinstance(view, RowViews):
                view.update_rows()

        self._rows = self.view_widgets()
        self._pile.contents = self._rows


class PrimarySplitEventView(RowViews):
    """Event view which is horizontally split, but with a 'primary' view.

    Provided keypress actions will be intercepted by the primary view (and focused).
    """

    def __init__(
        self,
        name: str,
        primary: EventView,
        *secondary: EventView,
        primary_key_actions: set[ACTIONS] | None = None,
    ):
        self._primary = primary
        self._primary_keys = set()
        if primary_key_actions:
            for action in primary_key_actions:
                self._primary_keys |= set(action_keypresses[action])

        super().__init__(primary.state, *list(reversed([primary, *secondary])), name=name)

    def keypress(
        self,
        size: tuple[()] | tuple[int] | tuple[int, int],
        key: str,
    ) -> str | None:
        if self._primary_keys and key in self._primary_keys:
            self._pile.focus = self._primary
            return self._primary.keypress(size, key)

        return super().keypress(size, key)
