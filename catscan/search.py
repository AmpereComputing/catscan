# Copyright (c) 2024-2026 Ampere Computing. All rights reserved.
# SPDX-License-Identifier: BSD-3-Clause

import re
from abc import ABC, abstractmethod
from collections import Counter
from collections.abc import Iterable
from enum import StrEnum
from re import Pattern
from sys import maxsize

from catscan.data import Event, EventData, EventStreamDataView
from catscan.util import glob_to_pattern


class MatchType(StrEnum):
    AUTO = "auto"
    INT = "int"
    STRING = "string"


class SearchPatterns(StrEnum):
    ANY = "*"


class Searcher(ABC):
    def search_row(self, row: str | int, row_fields: Iterable[str]) -> bool:
        """
        Check if row matches.

        Returns:
            True when a row is eligible to be matched by this search.

        """
        return True

    def search_event_row(self, row: EventData) -> bool:
        return self.search_row(row.key(), row.fields)

    def filtering_fields(self) -> bool:
        """
        If searcher is filtering based upon fields.

        Returns:
            True when this searcher is filtering based on the field names. If this returns 'False' for a Searcher, there is no need to call the 'search_field' method.

        """
        return False

    def search_field(self, name: str) -> bool:
        """
        If field can be searched.

        Returns:
            True when field named `field` is eligible to be matched by this search. This should *not* be called if filtering_fields() is returning False - it is not guaranteed to succeed or behave sensibly.

        """
        return True

    @abstractmethod
    def match(self, event: Event) -> bool:
        """
        Event matches.

        Returns:
            True when an event matches this search

        """

    @abstractmethod
    def field_match(self, name: str, field: int | str) -> bool:
        """
        Field matches.

        Returns:
            True when an event's field matches this search (presumes the event already was matched by match(), above)

        """

    @abstractmethod
    def __repr__(self) -> str:
        pass


class FilteringSearcher(Searcher):
    def __init__(self, search_rows: Iterable[str | int] | None, search_fields: Iterable[str] | None):
        self.search_rows = search_rows
        if search_fields is not None:
            self.search_fields = set(search_fields)
            self._orig_search_fields = search_fields
        else:
            self.search_fields = None

    @staticmethod
    def get_search_fields(esd: EventStreamDataView, search_fields: Iterable[str] | None) -> Iterable[str] | None:
        """
        Iterate through the potential field globs in EventStreamData, returning
        full field names matching the globs.
        """
        if not search_fields:
            return None

        match_fields = set()

        search_field_re_list = [glob_to_pattern(f) for f in search_fields]
        search_fields_re = re.compile("^((" + ")|(".join(search_field_re_list) + "))$")

        if search_fields_re.match("abbrev"):
            match_fields.add("abbrev")
        if search_fields_re.match("name"):
            match_fields.add("name")
        for row in esd:
            for name in row.fields:
                if search_fields_re.match(name):
                    match_fields.add(name)

        return match_fields

    def search_row(self, row: str | int, row_fields: set[str]) -> bool:
        matches_rows = self.search_rows is None or row in self.search_rows
        matches_fields = (
            not self.filtering_fields() or len(self.search_fields.intersection(row_fields | {"abbrev", "name"})) > 0
        )
        return matches_rows and matches_fields

    def filtering_fields(self) -> bool:
        return self.search_fields is not None

    def search_field(self, name: str) -> bool:
        return name in self.search_fields

    def _filter_repr(self) -> str:
        field_limiter = "" if self.search_fields is None else f" in {', '.join(self._orig_search_fields)}"
        row_limiter = "" if self.search_rows is None else f" in {', '.join(map(str, self.search_rows))}"
        return f"{field_limiter}{row_limiter}"


class TextSearcher(FilteringSearcher):
    def __init__(
        self,
        search_term: str,
        search_rows: Iterable[str | int] | None,
        search_fields: Iterable[str] | None,
        case_sensitive: bool,
        hexargs_re: Pattern,
    ):
        self.search_term = search_term
        self.case_sensitive = case_sensitive
        if not case_sensitive:
            self.search_term = self.search_term.lower()
            self.field_match = self.case_insensitive_field_match
            self.match = self.case_insensitive_match
        self.hexargs_re = hexargs_re
        super().__init__(search_rows, search_fields)

    def match(self, event: Event) -> bool:
        if (not self.filtering_fields() or self.search_field("abbrev")) and self.search_term in event.abbrev:
            return True
        if (not self.filtering_fields() or self.search_field("name")) and self.search_term in event.name:
            return True
        if self.search_term == SearchPatterns.ANY:
            return True
        for name, value in event.data.items():
            if self.filtering_fields() and not self.search_field(name):
                continue
            if self.hexargs_re.match(name):
                search_value = hex(int(value, base=0)) if isinstance(value, str) else hex(value)
            else:
                search_value = str(value)
            if self.search_term in search_value:
                return True
        return False

    def case_insensitive_match(self, event: Event) -> bool:
        if (not self.filtering_fields() or self.search_field("abbrev")) and self.search_term in event.abbrev.lower():
            return True
        if (not self.filtering_fields() or self.search_field("name")) and self.search_term in event.name.lower():
            return True
        if self.search_term == SearchPatterns.ANY:
            return True
        for name, value in event.data.items():
            if self.filtering_fields() and not self.search_field(name):
                continue
            if self.hexargs_re.match(name):
                search_value = hex(int(value, base=0)) if isinstance(value, str) else hex(value)
            else:
                search_value = str(value)
            if self.search_term in search_value.lower():
                return True
        return False

    def field_match(self, name: str, field: int | str) -> bool:
        return (not self.filtering_fields() or self.search_field(name)) and self.search_term in str(field)

    def case_insensitive_field_match(self, name: str, field: int | str) -> bool:
        return (not self.filtering_fields() or self.search_field(name)) and self.search_term in str(field).lower()

    def __repr__(self) -> str:
        filter_repr = self._filter_repr()
        case_sensitivity = "case-sensitive " if self.case_sensitive else ""
        return f'{case_sensitivity}search for string "{self.search_term}"{filter_repr}'


class IntSearcher(FilteringSearcher):
    def __init__(
        self,
        search_term: int,
        search_rows: Iterable[str | int] | None,
        search_fields: Iterable[str] | None,
        search_mask: int | None,
    ):
        self.search_term = search_term
        self.search_mask = search_mask
        if self.search_mask is not None:
            self.match = self.masked_match
            self.field_match = self.masked_field_match

        super().__init__(search_rows, search_fields)

    def match(self, event: Event) -> bool:
        for name, value in event.data.items():
            if self.filtering_fields() and not self.search_field(name):
                continue
            if isinstance(value, int) and value == self.search_term:
                return True
        return False

    def masked_match(self, event: Event) -> bool:
        for name, value in event.data.items():
            if self.filtering_fields() and not self.search_field(name):
                continue
            if isinstance(value, int) and ((value ^ self.search_term) & self.search_mask) == 0:
                return True
        return False

    def field_match(self, name: str, field: int | str) -> bool:
        return self.search_term in str(field)

    def masked_field_match(self, name: str, field: int | str) -> bool:
        return isinstance(field, int) and ((field ^ self.search_term) & self.search_mask) == 0

    def __repr__(self) -> str:
        filter_repr = self._filter_repr()
        mask = "" if self.search_mask is None else f" with mask {hex(self.search_mask)}"
        return f"search for int {hex(self.search_term)}/{self.search_term}{mask}{filter_repr}"


class PerPeriodSearcher(Searcher):
    """Searcher adapter for only matching events over some min_per_period.

    For example, if min_per_period is set to 4 no events will be matched for
    periods in which fewer than 4 matching events occur, and only events beyond
    4 will be matched in periods with at least 4 matching events.

    NOTE: depends on match() being called *in-order* which is currently the case.
    """

    def __init__(self, min_per_period: int, searcher: Searcher, *, period: int = 1):
        self.min_per_period = min_per_period
        self.searcher = searcher
        self.period = period
        self._last_period = 0
        self._seen = 0

    def search_row(self, row: str | int, row_fields: Iterable[str]) -> bool:
        return self.searcher.search_row(row, row_fields)

    def filtering_fields(self) -> bool:
        return False

    def search_field(self, name: str) -> bool:
        return False

    def match(self, event: Event) -> bool:
        if self.searcher.match(event):
            period = event.time / self.period
            if self._last_period == period:
                self._seen += 1
            else:
                self._seen = 1

            self._last_period = period
            return self._seen >= self.min_per_period

        return False

    def field_match(self, name: str, field: int | str) -> bool:
        return False

    def __repr__(self) -> str:
        return repr(self.searcher) + f", with at least {self.min_per_period} per cycle"


class EventStreamDataSearch:
    def __init__(
        self, esd: EventStreamDataView, searcher: Searcher, starting_point: int | Event, views: list[str] | None = None
    ):
        self.esd = esd
        self.searcher = searcher
        self.event_row_indices = {row_name: idx for idx, row_name in enumerate(esd.keys())}
        self._initialize_match_vars(starting_point)
        self.views = views

    def _initialize_match_vars(self, starting_point: int | Event) -> None:
        # Assume every event in each row matches to initialize row_matches
        # before finding the true match counts later
        self.row_matches = Counter(
            {row.key(): row.event_count for row in self.esd if self.searcher.search_event_row(row)}
        )

        # Initialize the 'search cursor' to the first match
        if (
            isinstance(starting_point, Event)
            and self.esd.key_of(starting_point) in self.row_matches
            and self.searcher.match(starting_point)
        ):
            self.search_cursor = starting_point
        else:
            self.search_cursor = self._next_match(starting_point)

        if self.search_cursor is None:
            # If we couldn't find any matches, we know there aren't any, and we
            # can early-out
            self.total_matches = 0
            self.row_matches = Counter()
            self.cursor_idx = None
            return

        # Search through _all_ events in matching rows, determining how many
        # total matches there are and how many occur before the current match
        self.cursor_idx = 0
        self.row_matches = Counter()
        self.start_time = maxsize
        self.end_time = 0
        cursor_event_key = self._event_key(self.search_cursor)
        for row in self.esd:
            if not self.searcher.search_event_row(row):
                continue
            for event in row[:]:
                if self.searcher.match(event):
                    self.row_matches[row.key()] += 1
                    self.start_time = min(self.start_time, event.time)
                    self.end_time = max(self.end_time, event.time)
                    if self._event_key(event) < cursor_event_key:
                        self.cursor_idx += 1

        self.total_matches = self.row_matches.total()
        self.start_time = self.start_time if self.total_matches > 0 else 0
        if self.end_time < self.start_time:
            raise ValueError("Invalid search range")

    def _event_key(self, event: Event) -> tuple[int, int, int]:
        """
        Get event key for ordering.

        An event is ordered "earlier" in the search order primarily if its time
        is earlier, then the index of the rows, and finally by the event
        indices themselves (for two events which begin at the same time and are
        in the same row)
        """
        key = self.esd.key_of(event) if isinstance(event, Event) else event.key()
        return (event.time, self.event_row_indices[key], event.id)

    def _next_match(self, starting_point: int | Event) -> Event | None:
        original_start_ps = starting_point if isinstance(starting_point, int) else starting_point.time
        searching_ps_diff = 128 * 400  # start with granularity of ~128 cycles
        start_ps = original_start_ps
        end_ps = start_ps + searching_ps_diff
        earliest_match = None
        while start_ps <= self.esd.last_time:
            earliest_match = self._next_match_until(starting_point, end_ps)
            if earliest_match is not None:
                return earliest_match
            searching_ps_diff *= 4
            starting_point = start_ps = end_ps
            end_ps = start_ps + searching_ps_diff

        start_ps = self.esd.first_time
        end_ps = start_ps + searching_ps_diff
        while start_ps <= original_start_ps:
            earliest_match = self._next_match_until(start_ps, end_ps)
            if earliest_match is not None:
                return earliest_match
            searching_ps_diff *= 4
            start_ps = end_ps
            end_ps = start_ps + searching_ps_diff

        return None

    def _prev_match(self, starting_point: int | Event) -> Event | None:
        original_start_ps = starting_point if isinstance(starting_point, int) else starting_point.time
        searching_ps_diff = 128 * 400  # start with granularity of ~128 cycles
        start_ps = original_start_ps
        end_ps = start_ps - searching_ps_diff
        earliest_match = None
        while start_ps >= self.esd.first_time:
            earliest_match = self._prev_match_until(starting_point, end_ps)
            if earliest_match is not None:
                return earliest_match
            searching_ps_diff *= 4
            starting_point = start_ps = end_ps
            end_ps = start_ps - searching_ps_diff

        start_ps = self.esd.last_time
        end_ps = start_ps - searching_ps_diff
        while start_ps >= original_start_ps:
            earliest_match = self._prev_match_until(start_ps, end_ps)
            if earliest_match is not None:
                return earliest_match
            searching_ps_diff *= 4
            start_ps = end_ps
            end_ps = start_ps - searching_ps_diff

        return None

    def _next_match_until(self, starting_point: int | Event, end_ps: int) -> Event | None:
        earliest_match = None
        for row, _ in self.row_matches.most_common():
            next_match = self._next_row_match(row, starting_point, end_ps, reverse=False)
            if next_match is not None and (
                earliest_match is None or self._event_key(next_match) < self._event_key(earliest_match)
            ):
                earliest_match = next_match
                end_ps = earliest_match.time + 1
        return earliest_match

    def _prev_match_until(self, starting_point: int | Event, end_ps: int) -> Event | None:
        latest_match = None
        for row, _ in self.row_matches.most_common():
            prev_match = self._next_row_match(row, starting_point, end_ps, reverse=True)
            if prev_match is not None and (
                latest_match is None or self._event_key(prev_match) > self._event_key(latest_match)
            ):
                latest_match = prev_match
                end_ps = latest_match.time - 1
        return latest_match

    def _next_row_match(
        self, row: str, starting_point: int | Event, end_ps: int, reverse: bool = False
    ) -> Event | None:
        # Convert starting_point to picoseconds if not already
        if isinstance(starting_point, Event):
            start_ps = starting_point.time
            starting_event_key = self._event_key(starting_point)
        else:
            start_ps = starting_point
            starting_event_key = None

        if reverse:
            for event in self.esd.get(row)[start_ps:end_ps:-1]:
                if self.searcher.match(event) and (
                    starting_event_key is None or self._event_key(event) < starting_event_key
                ):
                    return event
        else:
            for event in self.esd.get(row)[start_ps:end_ps]:
                if self.searcher.match(event) and (
                    starting_event_key is None or self._event_key(event) > starting_event_key
                ):
                    return event
        return None

    def next(self) -> Event | None:
        if self.total_matches == 0:
            return None

        self.search_cursor = self._next_match(self.search_cursor)
        self.cursor_idx = (self.cursor_idx + 1) % self.total_matches
        return self.search_cursor

    def prev(self) -> Event | None:
        if self.total_matches == 0:
            return None

        self.search_cursor = self._prev_match(self.search_cursor)
        self.cursor_idx = (self.cursor_idx - 1 + self.total_matches) % self.total_matches
        return self.search_cursor
