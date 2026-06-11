# Copyright (c) 2019-2026 Ampere Computing. All rights reserved.
# SPDX-License-Identifier: BSD-3-Clause

"""
Classes to interact with events in Catscan.
"""

import re
from abc import ABC, abstractmethod
from collections.abc import Callable, Iterator
from itertools import chain

from perf_streams.event_stream import Event

from catscan.events import EventSpecification

DEFAULT_PERIOD = 300


def trace_spec(arg: str) -> EventSpecification:
    return EventSpecification(arg)


def split_spec(arg: str) -> tuple[EventSpecification, Callable]:
    if "=" not in arg:
        raise ValueError("Invalid split, expected '='")

    event_spec, fmt = arg.split("=")
    event = EventSpecification(event_spec)
    names = tuple(zip(event.factors, event.long_factors))
    lowercase = set(re.findall(r"\{([\w_\.]+):s?ls?\}", fmt))
    fmt = re.sub(r"\{([\w_\.]+):(.*?)s?ls?(.*?)\}", r"{\1:\2\3}", fmt)

    def rename_event_from_data(ev: Event):
        values = {
            short_name: ev.data[long_name].lower()
            if short_name in lowercase and isinstance(ev.data[long_name], str)
            else ev.data[long_name]
            for short_name, long_name in names
        }
        ev.name = fmt.format_map(values)

    return event, rename_event_from_data


class EventFilter(ABC):
    """Generic Event filter interface."""

    CacheNames = set[str]
    OptionalCacheNames = CacheNames | None

    def events(self) -> list[EventSpecification]:
        """Event specifications covered by filter."""
        return []

    @abstractmethod
    def __call__(self, event: Event) -> tuple[bool | None, OptionalCacheNames]:
        """Process/filter event, included/excluded if bool returned."""


class EventFilters:
    """Collection of event filters with caching."""

    CacheResult = tuple[bool, str | None]

    def __init__(self, filters: list[EventFilter] | None = None, default_inclusion: bool = False):
        self._cache: dict[
            str, tuple[EventFilter.CacheNames, dict[str, EventFilters.CacheResult]] | EventFilters.CacheResult
        ] = {}
        self._filters = filters or []
        self._default_inclusion = default_inclusion

    def events(self) -> list[EventSpecification]:
        """Get all event specifications for all filters."""
        events = []
        for event_filter in self._filters:
            events.extend(event_filter.events())

        return events

    def _apply(self, event: Event) -> tuple[bool | None, EventFilter.CacheNames]:
        included = None
        cache_data_names = set()
        for event_filter in self._filters:
            filter_includes, cache = event_filter(event)
            if isinstance(cache, set):
                cache_data_names |= cache
            elif cache is not None:
                cache_data_names.add(cache)

            if filter_includes is not None:
                if included is None:
                    included = filter_includes
                else:
                    included &= filter_includes

        return included, cache_data_names

    def _hash_event(self, event: Event, names: EventFilter.CacheNames) -> int:
        return hash(tuple(event.data[name] for name in names))

    def _apply_to(self, event: Event, included: bool | None, name_change: str | None = None) -> bool:
        if name_change:
            event.name = name_change
        return self._default_inclusion if included is None else included

    def apply(self, event: Event) -> bool:
        """Apply all filters to event."""
        if not self._filters:
            return True

        event_hash = None
        if event.name in self._cache:
            names_or_included, cache_or_name = self._cache[event.name]
            if isinstance(names_or_included, set):
                event_hash = self._hash_event(event, names_or_included)
                if event_hash in cache_or_name:
                    return self._apply_to(event, *cache_or_name[event_hash])
            else:
                return self._apply_to(event, names_or_included, cache_or_name)

        original_name = event.name
        included, cache_data_names = self._apply(event)
        result_value = (included, event.name if included and original_name != event.name else None)
        if cache_data_names:
            if event_hash is None:
                event_hash = self._hash_event(event, cache_data_names)
            if original_name not in self._cache:
                self._cache[original_name] = [cache_data_names, {}]
            self._cache[original_name][1][event_hash] = result_value
        else:
            self._cache[original_name] = result_value

        return self._apply_to(event, included)


class EventIncluder(EventFilter):
    """Explicit event includer."""

    def __init__(self, includes: list[EventSpecification]):
        self._events = includes

    def events(self) -> list[EventSpecification]:
        return self._events

    def __call__(self, event: Event) -> tuple[bool | None, None]:
        for include in self._events:
            if include(event.name):
                return True, None

        return None, None


class EventExcluder(EventFilter):
    """Explicit event excluder."""

    def __init__(self, excludes: list[EventSpecification]):
        self._excludes = excludes

    def __call__(self, event: Event) -> tuple[bool | None, None]:
        for exclude in self._excludes:
            if exclude(event.name):
                return False, None

        return None, None


class EventRenamer(EventFilter):
    """Event renamer."""

    def __init__(self, renames: list[Callable[[str], str]]):
        self._renames = renames

    def __call__(self, event: Event) -> tuple[bool | None, None]:
        original_name = event.name
        for rename in self._renames:
            event.name = rename(event.name)

        return True if original_name != event.name else None, None


class EventSplitter(EventFilter):
    """Event splitter, splits factored event into discretely named event."""

    def __init__(
        self,
        splits: list[tuple[EventSpecification, Callable[[Event], None]]] | None = None,
        include: bool = False,
    ):
        self._splits = splits or []
        self._include = include

    def events(self) -> list[EventSpecification]:
        return [e for e, _ in self._splits]

    def __call__(self, event: Event) -> tuple[bool | None, EventFilter.CacheNames]:
        cache_data_names = set()
        for event_spec, gen_func in self._splits:
            if event_spec(event.name):
                gen_func(event)
                cache_data_names = set(event_spec.long_factors)
                if self._include:
                    return True, cache_data_names
                break

        return None, cache_data_names


class Occupancy:
    def __init__(self, definition: str):
        self.name, events = definition.split(":")
        parts = events.split(",")
        self.acquire, self.release = parts[0:2]
        self.data = parts[2] if len(parts) > 2 else ".occupancy"
        self.count = 0


class OccupancyTracker:
    def __init__(self, occupancy_list: list[str]) -> None:
        self.acquire = {}
        self.release = {}
        self.rows = []
        for o in occupancy_list:
            occupancy = Occupancy(o)
            self.acquire[occupancy.acquire] = occupancy
            self.release[occupancy.release] = occupancy
            self.rows.append(occupancy.name)

    @property
    def events(self) -> Iterator[Event]:
        return chain.from_iterable([o.acquire, o.release] for o in self.acquire.values())

    def acquired(self, event: Event) -> int | None:
        if occupancy := self.acquire.get(event.name):
            if count := ResourceView.value_with_suffix(event, occupancy.data):
                occupancy.count = count
            else:
                occupancy.count += 1
            return occupancy
        return None

    def released(self, event: Event) -> int | None:
        if occupancy := self.release.get(event.name):
            if count := ResourceView.value_with_suffix(event, occupancy.data):
                occupancy.count = count
            elif occupancy.count > 0:
                occupancy.count -= 1
            return occupancy
        return None


class ResourceView:
    @staticmethod
    def value_with_suffix(event: Event, suffix: str) -> str | None:
        for name, value in event.data.items():
            if name.endswith(suffix):
                return value

        return None

    @staticmethod
    def setup_tx_data(mapping: list[str]) -> dict[str, dict[str, str]]:
        tx_data = {}
        item_re = re.compile(r"([^:]+):([^=]+)(=(\S+))?")
        for item in mapping:
            m = item_re.match(item)
            if m:
                event_name, data_name, alias_name = m.group(1, 2, 4)
                if event_name not in tx_data:
                    tx_data[event_name] = {}

                alias_name = alias_name or data_name
                tx_data[event_name][data_name] = alias_name
                if "." not in data_name and "." in event_name:
                    potential_full_name = ".".join(event_name.split(".")[:-1] + [data_name])
                    tx_data[event_name][potential_full_name] = alias_name
            else:
                raise RuntimeError(f'transaction data spec did not look like <event>:<data>[=<alias>] (got "{item}")')
        return tx_data
