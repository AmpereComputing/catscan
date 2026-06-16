# Copyright (c) 2024 Ampere Computing. All rights reserved.
# SPDX-License-Identifier: BSD-3-Clause

from collections.abc import Hashable, Iterable, Mapping
from fractions import Fraction
from inspect import get_annotations
from typing import Any, NamedTuple

from catscan.data import Event, EventData, Transaction
from catscan.search import Searcher


class HashableFrozenDict(Mapping, Hashable):
    """
    Typical dictionaries in python are not hashable. This is problematic if you
    want to use them as indices into a cache. Provide a 'frozen' dictionary
    implementation ('frozen' meaning its contents cannot _easily_ be modified
    once it is created), so that we can use dictionaries as indices into
    caching functions.
    """

    def __init__(self, *args: Any, **kwargs: Any):
        self._dict = dict(*args, **kwargs)
        for k, v in self._dict.items():
            if not isinstance(k, Hashable):
                raise TypeError(f"unhashable type: '{type(k)}'")
            if not isinstance(v, Hashable):
                raise TypeError(f"unhashable type: '{type(v)}'")
        self._cached_hash = hash(tuple(sorted(self._dict.items())))

    def copy_with(self, key: Hashable, value: Hashable) -> "HashableFrozenDict":
        if not isinstance(key, Hashable):
            raise TypeError(f"unhashable type: '{type(key)}'")
        if not isinstance(value, Hashable):
            raise TypeError(f"unhashable type: '{type(value)}'")
        new_dict = self._dict.copy()
        new_dict[key] = value
        return HashableFrozenDict(new_dict)

    def copy_without(self, key: Hashable) -> "HashableFrozenDict":
        if key not in self._dict:
            return self
        new_dict = self._dict.copy()
        del new_dict[key]
        return HashableFrozenDict(new_dict)

    def __getitem__(self, key: Any) -> Any:
        return self._dict.__getitem__(key)

    def __iter__(self):  # noqa: ANN204
        return self._dict.__iter__()

    def __len__(self) -> int:
        return len(self._dict)

    def __hash__(self) -> int:
        return self._cached_hash

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, HashableFrozenDict):
            return False
        return tuple(sorted(self._dict.items())) == tuple(sorted(other._dict.items()))


class Selection:
    """
    Selection represents a user's current selection in the UI. This can be
    either a single event or a range of time.
    """

    def __init__(
        self,
        event_row: str | int | None = None,
        event: Event | None = None,
        duration: int | None = None,
        time_range: tuple[int, int] | None = None,
        within_transaction: bool = False,
        view: str | None = None,
    ):
        assert not (event and time_range)  # cannot simultaneously select single event and time range
        assert event_row or not (event or time_range)  # must specify name if either of other two is specified
        assert duration or not event  # a duration must be specified if an event is

        self._event_row = event_row
        self._event = event
        self._event_duration = duration
        self._time_range = time_range
        self._view = view
        self._within_transaction = within_transaction

    def assign_view(self, view: str):
        self._view = view

    @property
    def event_row(self) -> str | int:
        return self._event_row

    def is_event(self) -> bool:
        return self._event is not None

    def is_time_range(self) -> bool:
        return self._time_range is not None

    def is_selected(self, ed: EventData) -> bool:
        if self._event_row is not None:
            return self._event_row == ed.key()
        if self._event is not None:
            return self._event.name == ed.name
        return False

    def within_transaction(self) -> bool:
        return self._within_transaction

    @property
    def event(self) -> Event:
        assert self.is_event()
        return self._event

    @property
    def view(self) -> str:
        return self._view

    @property
    def start_ps(self) -> int:
        """
        The starting time of the selection (inclusive), in picoseconds.
        """
        assert self.is_event() or self.is_time_range()
        if self.is_event():
            return self.event.time
        assert self.is_time_range()
        return self._time_range[0]

    @property
    def end_ps(self) -> int:
        """
        The ending time of the selection (exclusive), in picoseconds.
        """
        assert self.is_event() or self.is_time_range()
        if self.is_event():
            return self.event.time + self._event_duration
        assert self.is_time_range()
        return self._time_range[1]

    def __bool__(self) -> bool:
        return self.event_row is not None

    def extend(self, other: "Selection") -> "Selection":
        """
        Combine two selections into one.
        """
        if self == other or not other:
            return self
        if not self:
            return other

        # At this point, we know we need to combine the two selections to form
        # one larger selection, which necessarily needs to become
        # time-range-based. It is possible that the two are on different event
        # rows if the user has clicked on another when creating the second
        # Selection, but for now we only support keeping the row of the
        # original selection while extending the selected time range.
        return Selection(
            event_row=self.event_row,
            time_range=(
                min(self.start_ps, other.start_ps),
                max(self.end_ps, other.end_ps),
            ),
            within_transaction=self.within_transaction(),
            view=self.view,
        )

    def adjust_within_row(self, event: Event, duration: int | None = None) -> "Selection":
        """Move to different event within the same row."""
        return Selection(
            event_row=self.event_row,
            event=event,
            duration=duration or self._event_duration,
            within_transaction=self.within_transaction(),
            view=self.view,
        )

    def __repr__(self) -> str:
        if not self:
            return "<no selection>"
        if self.is_event():
            return f"<selected event {self.event}>"
        return f"<selected row {self.event_row} from {self.start_ps}ps to {self.end_ps}ps>"


class CatscanState(NamedTuple):
    """
    CatscanState holds the main state of the application. (Almost) whenever the
    user takes an action in the UI, a copy of this state object is made using
    the `copy_with` method with whatever updates to the global state were
    required. This state is then sent to any components which need to observe
    the updated state.
    """

    has_focus: bool  # True if the application has focus
    loading: bool  # True if the application is loading
    ps_per_cycle: int  # The number of picoseconds per 'cycle'
    column_header_width: int  # The width of the first column holding the event names
    cycles_per_char: Fraction  # The number of cycles summarized per character
    start_ps: int  # The first picosecond displayed on the left (aligned to the current cycles_per_char)
    start_row: int  # The first row of output displayed at the top
    expand_rows: bool  # Whether the rows of events should be displayed in their 'expanded' form
    selection: Selection  # The event or row/time range selected
    sort_event_keys: bool  # Whether to sort event data items by their keys
    highlighted_transactions: HashableFrozenDict  # A mapping of highlighted txid's to their highlight colors
    marked_events: HashableFrozenDict  # A mapping of marks to their associated events
    searcher: Searcher | None  # The current search, if any
    messages: list  # Messages to display for the user
    show_help: bool  # Showing the help message

    @property
    def ps_per_char(self) -> Fraction:
        return self.ps_per_cycle * self.cycles_per_char

    def copy_with(self, **kwargs: Any) -> "CatscanState":
        """
        Return a copy of the state object, with the members in kwargs updated
        with their respective values. Check the types being assigned match the
        type annotations to ensure the application state is always consistent.
        """
        for key, value in kwargs.items():
            annotations = get_annotations(CatscanState)
            if key not in annotations:
                raise KeyError(f"{key} not valid for {type(self)}")
            expected_type = annotations[key]
            if not isinstance(value, expected_type):
                raise TypeError(
                    f"Expected CatscanState member {key} to be of type {expected_type}, but found {type(value)} instead"
                )
            if key == "highlighted_transactions":
                for txid, color in value.items():
                    if not isinstance(txid, int):
                        raise TypeError(f"highlighted_transaction keys must be of type int, found {type(txid)}")
                    if not isinstance(color, int):
                        raise TypeError(
                            f"highlighted_transaction color values must be of type int, found {type(color)}"
                        )
            elif key == "marked_events":
                for mark, event in value.items():
                    if not isinstance(mark, str):
                        raise TypeError(f"marked_events keys must be of type str, found {type(mark)}")
                    if not isinstance(event, Event):
                        raise TypeError(f"marked_events values must be of type Event, found {type(event)}")

        return self._replace(**kwargs)
