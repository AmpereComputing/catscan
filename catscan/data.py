# Copyright (c) 2024 Ampere Computing. All rights reserved.
# SPDX-License-Identifier: BSD-3-Clause

import bisect
import copy
import gzip
import heapq
from array import array
from collections import Counter
from collections.abc import Iterable, Iterator
from functools import cached_property
from io import IOBase
from itertools import chain, islice
from math import gcd

try:
    import lzma
except:
    from backports import lzma
import logging
import os
import pickle
import zlib
from collections.abc import Callable
from enum import StrEnum, auto
from typing import Any, Self

from perf_streams.event_stream import Event, EventStreamReader

from catscan.events import EventSpecification, trace_events
from catscan.events.mapping import Mapper

# The number of unique colors used for events
NUM_EVENT_COLORS = 8


class DataView(StrEnum):
    """Data view types."""

    RESOURCE = auto()
    TRANSACTIONS = auto()


class CatscanEvent(Event):
    def map_and_abbreviate(self, mapper: Mapper) -> None:
        self.abbrev = mapper.event_name_to_abbrev(self)
        self.color_idx = zlib.adler32(self.abbrev.encode()) % NUM_EVENT_COLORS
        mapper.modify_args(self.data)


class Transaction:
    """Class to hold per-transaction information."""

    def __init__(self, event: Event) -> None:
        self.txid = event.data["txid"]
        self.start_time = event.time
        self.end_time = event.time
        self.parents = set()
        self.children = set()
        self.data = {}

    def add_event(self, event: Event) -> None:
        self.start_time = min(event.time, self.start_time)
        self.end_time = max(event.time, self.end_time)

    def __repr__(self) -> str:
        return f"txid={self.txid} start={self.start_time:,}ps end={self.end_time:,}ps parents={self.parents} children={self.children}"


class EventData:
    def __init__(
        self,
        name: str,
        group: str | None = None,
        short_name: str | None = None,
        original_name: str | None = None,
    ) -> None:
        self.name = name
        self.group = group or ""
        self.short_name = short_name or name
        self.original_name = original_name or name

        # Sorted array of all the times (in picoseconds) at which events of
        # this type occur
        self.times = array("q")  # 'q' == signed long long

        # Dictionary indexed by time (picoseconds), where each value is a list
        # of events occurring at that time
        self.events: dict[int, list[Event]] = {}

        # Set containing the names of all data fields which occur for events of
        # this type
        self.fields = set()

        # Counter object containing the unique abbreviations which occur for
        # events of this type, along with their frequencies
        self.abbreviations = Counter()

        # Keep statistics that may be useful later
        self._event_count = 0
        self._max_per_time = 0

    def key(self) -> str:
        return self.name

    @property
    def event_count(self) -> int:
        return self._event_count

    @property
    def first_time(self) -> int:
        return self.times[0]

    @property
    def last_time(self) -> int:
        return self.times[-1]

    @property
    def start_time(self) -> int:
        return self.first_time

    @property
    def end_time(self) -> int:
        return self.last_time

    def insert(self, event: Event) -> None:
        # Keep the abbreviations and data fields up-to-date
        self.abbreviations[event.abbrev] += 1
        self.fields |= event.data.keys()

        if event.time in self.events:
            self.events[event.time].append(event)
            self._max_per_time = max(self._max_per_time, len(self.events[event.time]))
        else:
            bisect.insort(self.times, event.time)
            self.events[event.time] = [event]
            self._max_per_time = max(self._max_per_time, 1)
        self._event_count += 1

    def youngest_older(self, event: Event) -> Event | None:
        index_in_time = self.events[event.time].index(event)
        if index_in_time > 0:
            return self.events[event.time][index_in_time - 1]
        time_idx = bisect.bisect_left(self.times, event.time)
        if time_idx > 0:
            time = self.times[time_idx - 1]
            return self.events[time][-1]
        return None

    def oldest_younger(self, event: Event) -> Event | None:
        index_in_time = self.events[event.time].index(event)
        if index_in_time < len(self.events[event.time]) - 1:
            return self.events[event.time][index_in_time + 1]
        time_idx = bisect.bisect_left(self.times, event.time)
        if time_idx < len(self.times) - 1:
            time = self.times[time_idx + 1]
            return self.events[time][0]
        return None

    def closest_to(self, time: int, direction: str | None = None) -> Event | None:
        """
        Return the event closest to the supplied time. When multiple events
        exist in the closest time containing events, the event "closest" will
        be chosen (or the event in the middle if there's an exact time match).

        If a direction (forwards/backwards) is provided, then it will effectively
        return the next/previous event, respectively.
        """
        if not self.times:
            return None

        time_idx = bisect.bisect_left(self.times, time)
        if time_idx >= len(self.times):
            closest_time = self.times[-1]
        elif time_idx == 0:
            closest_time = self.times[0]
        elif direction == "backwards" or (
            direction is None and time - self.times[time_idx - 1] < self.times[time_idx] - time
        ):
            closest_time = self.times[time_idx - 1]
        else:
            closest_time = self.times[time_idx]

        if time > closest_time:
            return self.events[closest_time][-1]
        if time < closest_time:
            return self.events[closest_time][0]
        middle_index = (len(self.events[closest_time]) - 1) // 2
        return self.events[closest_time][middle_index]

    def __len__(self) -> int:
        return self._event_count

    def max_events_per_time(self) -> int:
        return self._max_per_time

    def __getitem__(self, key: int | slice) -> Iterator[Event]:
        if isinstance(key, slice):
            max_step = 1 if key.step is None else key.step
            assert isinstance(max_step, int) and max_step != 0
            reverse = max_step < 0

            # start_idx is the index of the first time within the slice
            if key.start is None:
                start_idx = len(self.times) - 1 if reverse else 0
            elif reverse:
                start_idx = bisect.bisect_right(self.times, key.start) - 1
            else:
                start_idx = bisect.bisect_left(self.times, key.start)
            # stop_idx is the index of the first time *after* the slice
            if key.stop is None:
                stop_idx = -1 if reverse else len(self.times)
            elif reverse:
                stop_idx = bisect.bisect_right(self.times, key.stop) - 1
            else:
                stop_idx = bisect.bisect_left(self.times, key.stop)

            # Now, yield all the events within the given range
            step = 0
            increment = -1 if reverse else 1
            for idx in range(start_idx, stop_idx, increment):
                events = self.events[self.times[idx]]
                for event in reversed(events) if reverse else events:
                    step += increment
                    if step == max_step:
                        step = 0
                        yield event
        else:
            for event in self.events[key]:
                yield event


class TransactionEventData(EventData):
    def __init__(self, tx: Transaction, spec: "TransactionSpecification", name: str | int | None = None):
        super().__init__(hex(name) if isinstance(name, int) else name)
        self._ancestry = []
        self.transaction = tx
        self.spec = spec
        self._start_event_time = None
        self._end_event_time = None

    @property
    def txid(self) -> int:
        return self.transaction.txid

    @property
    def ancestry(self) -> list[int]:
        return self._ancestry + [self.txid]

    @property
    def level(self) -> int:
        return len(self._ancestry)

    def key(self) -> int:
        return self.txid

    def start(self, event: Event) -> None:
        self._start_event_time = event.time

    @property
    def started(self) -> bool:
        return self._start_event_time is not None

    def end(self, event: Event) -> None:
        self._end_event_time = event.time

    @property
    def ended(self) -> bool:
        return self._end_event_time is not None

    @property
    def start_time(self) -> int:
        return self._start_event_time or self.first_time

    @property
    def end_time(self) -> int:
        return self._end_event_time or self.last_time

    def process_event(self, event: Event) -> None:
        self.spec.process(self, event)


class TransactionSpecification:
    DEFAULT_NAME = "[unknown]"
    DEFAULT_START = trace_events.trace_spec("start_transaction")
    DEFAULT_END = trace_events.trace_spec("end_transaction")

    def __init__(
        self,
        name: str | None,
        start: trace_events.EventSpecification | None = None,
        end: trace_events.EventSpecification | None = None,
    ):
        self.name = name or self.DEFAULT_NAME
        self.start = start or self.DEFAULT_START
        self.end = end or self.DEFAULT_END

    def event_specifications(self) -> list[trace_events.EventSpecification]:
        return [
            trace_events.trace_spec(self.name),
            self.start,
            self.end,
        ]

    def create_row(self, tx: Transaction, event: Event) -> TransactionEventData:
        transaction = TransactionEventData(
            tx,
            self,
            name=event.abbrev if self.name == "abbrev" else event.data.get(self.name),
        )
        transaction.start(event)
        return transaction

    def process(self, transaction: TransactionEventData, event: Event) -> None:
        if transaction.name in (None, self.DEFAULT_NAME) and self.name in event.data:
            transaction.original_name = transaction.short_name = transaction.name = event.data[self.name]
        if self.end(event.name):
            transaction.end(event)


class EventStreamData:
    def __init__(
        self,
        filename: str,
        *,
        mapper: Mapper | None = None,
        event_filters: trace_events.EventFilters | None = None,
        events: Iterable[EventSpecification] | None = None,
        post_to_tx: list[str] | None = None,
        pull_from_tx: list[str] | None = None,
        occupancy: list[str] | None = None,
    ) -> None:
        self.source = filename
        self.mapper = mapper or Mapper([], [], [])
        self._events = events or []
        self._event_filters = event_filters or trace_events.EventFilters()

        self.event_rows: dict[str, EventData] = {}
        self.event_row_keys: tuple[int, ...] = ()
        self.transactions: dict[int, Transaction] = {}

        self.transaction_event_rows: dict[int, TransactionEventData] = {}
        self._transaction_specifications: list[TransactionSpecification] = []

        self._post_to_tx = trace_events.ResourceView.setup_tx_data(post_to_tx or [])
        self._pull_from_tx = trace_events.ResourceView.setup_tx_data(pull_from_tx or [])
        self._occupancy = trace_events.OccupancyTracker(occupancy or [])

        post_data = set(chain.from_iterable(data.values() for data in self._post_to_tx.values()))
        pull_data = set(chain.from_iterable(data.keys() for data in self._pull_from_tx.values()))
        self._post_to_tx_only = post_data - pull_data

        self._event_count = 0
        self.max_event_id = None
        self._first_time = None
        self._last_time = None
        self.finalized = False

    @classmethod
    def create_empty(cls) -> Self:
        empty = cls("empty")
        empty.finalize()
        return empty

    @property
    def empty(self) -> bool:
        return self.source == "empty"

    def add_transaction_view(
        self,
        name: str | list[str],
        start: trace_events.EventSpecification | list[trace_events.EventSpecification | None] | None = None,
        end: trace_events.EventSpecification | list[trace_events.EventSpecification | None] | None = None,
    ) -> list[trace_events.EventSpecification]:
        name = name if isinstance(name, list) else [name]
        start = start if isinstance(start, list) else [start]
        end = end if isinstance(end, list) else [end]

        if len(name) != len(start) or len(name) != len(end):
            raise ValueError(
                f"tx_name ({len(name)}), tx_start ({len(start)}), and tx_end ({len(end)}) are not the same length"
            )

        self._transaction_specifications = [
            TransactionSpecification(n, start=s, end=e) for n, s, e in zip(name, start, end)
        ]

        return list(chain.from_iterable(spec.event_specifications() for spec in self._transaction_specifications))

    @property
    def views(self) -> set[str]:
        if self._transaction_specifications:
            return {DataView.TRANSACTIONS}
        return {DataView.RESOURCE}

    def finalize(self):
        """
        Mark this data as "complete" - meaning no further events will be
        inserted and it is ready to be consumed.
        """
        if DataView.RESOURCE in self.views:
            self.event_row_keys = self._gen_event_row_keys()
            if not self.empty and not self.event_row_keys:
                logging.warning("No (unfiltered) events in event stream")

        if DataView.TRANSACTIONS in self.views:
            for tx in self.transaction_event_rows.values():
                ancestry = []
                ptx = tx.transaction
                while ptx and ptx.parents:
                    txid = list(ptx.parents)[0]
                    if txid in self.transaction_event_rows:
                        ancestry.append(txid)
                    ptx = self.transactions.get(txid)

                tx._ancestry = list(reversed(ancestry))

            self.transaction_event_rows = dict(sorted(self.transaction_event_rows.items(), key=lambda e: e[1].ancestry))
            if not self.empty and not self.transaction_event_rows:
                logging.warning("No (unfiltered) transactions in event stream")

        del self._events

        # Remove unneeded data
        if self._post_to_tx_only:
            for transaction in self.transactions.values():
                transaction.data = {k: v for k, v in transaction.data.items() if k in self._post_to_tx_only}

        self.finalized = True

    def copy_with_events(self, new_events: list[Event], insert_after: None | int | str = None) -> "EventStreamData":
        """
        Once EventStreamData has been finalized and distributed, we do not
        modify it in-place because doing so can lead to inconsistencies (i.e.
        some usages expect it to remain unchanged). However, we can make a copy
        of it and replace it everywhere.
        """
        new_esd = copy.copy(self)
        new_esd.finalized = False

        previous_rows = set(self.event_row_keys)
        for event in new_events:
            assert event.name not in previous_rows
            new_esd.insert(event)

        if DataView.RESOURCE in self.views:
            # Now, determine which rows, if any, are new and insert them at the
            # requested insertion point
            new_rows = [event_name for event_name in self.event_rows if event_name not in self.event_row_keys]

            if isinstance(insert_after, str):
                insert_after = self.event_row_keys.index(insert_after)
            else:
                insert_after = insert_after or len(self.event_row_keys)

            new_esd.event_row_keys = (
                new_esd.event_row_keys[0 : insert_after + 1]
                + tuple(new_rows)
                + new_esd.event_row_keys[insert_after + 1 :]
            )

        new_esd.finalized = True
        return new_esd

    def _gen_event_row_keys(self) -> tuple[str]:
        # Create dictionary of group names to group indices
        groups = {g: i for i, g in enumerate(sorted({row.group for _, row in self.event_rows.items()}))}

        # Sort the events primarily by group name and second by event 'index'
        # (the order they were specified on the command-line or in a config
        # file)
        sort_keys = []
        for event_name, event_row in self.event_rows.items():
            for event_spec in self._events:
                if event_spec(event_row.original_name):
                    sort_keys.append(((groups[event_row.group], event_spec.index), event_name))

        sorted_list = list(zip(*sorted(sort_keys)))
        # Remove duplicates while maintaining order
        return tuple(dict.fromkeys(sorted_list[1]) if sorted_list else [])

    @property
    def event_count(self) -> int:
        return self._event_count

    @property
    def first_time(self) -> int:
        return self._first_time or 0

    @property
    def last_time(self) -> int:
        return self._last_time or 0

    @cached_property
    def period(self) -> int:
        periods = Counter()

        def calculate_periods(rows: Iterable[EventData]) -> None:
            times = heapq.merge(*(row.times for row in rows))
            try:
                prev = next(times)
                for current in times:
                    periods[current - prev] += 1
            except StopIteration:
                pass

        if DataView.RESOURCE in self.views:
            calculate_periods(self.event_rows.values())
        if DataView.TRANSACTIONS in self.views:
            calculate_periods(self.transaction_event_rows.values())

        return gcd(*[p[0] for p in sorted(periods.items(), key=lambda x: x[1], reverse=True)])

    def _account_transaction(self, event: Event) -> Transaction | None:
        if "txid" not in event.data:
            return None
        txid = event.data["txid"]

        if txid not in self.transactions:
            self.transactions[txid] = Transaction(event)
        else:
            self.transactions[txid].add_event(event)

        tx = self.transactions[txid]

        # Track parent/child relationships
        if event.name == "start_transaction" and "parent" in event.data:
            parent_txid = event.data["parent"]
            tx.parents.add(parent_txid)
            if parent_txid in self.transactions:
                self.transactions[parent_txid].children.add(txid)

        # Post any data items we are tracking on the transaction itself
        if post_for_this_event := self._post_to_tx.get(event.name, None):
            for k, v in event.data.items():
                if k in post_for_this_event:
                    tx.data[post_for_this_event[k]] = v

        # Pull any data items we are tracking through the transaction for this
        # event
        if pull_for_this_event := self._pull_from_tx.get(event.name, None):
            pull_for_this_event = pull_for_this_event.copy()
            tried_txes = set()
            txes_to_try = {txid}
            while txes_to_try and pull_for_this_event:
                trying_txid = txes_to_try.pop()
                tried_txes.add(trying_txid)
                if trying_txid not in self.transactions:
                    continue
                pull_tx = self.transactions[trying_txid]

                for k, v in pull_tx.data.items():
                    if k in pull_for_this_event:
                        event.data[pull_for_this_event[k]] = v
                        del pull_for_this_event[k]
                txes_to_try = txes_to_try.union(pull_tx.parents.difference(tried_txes))

        return tx

    def _get_row(self, event: Event, original_event_name: str, *, group: str | None = None) -> EventData:
        if event.name not in self.event_rows:
            event_group, short_name = self.mapper.process(event.name, default_group=group)
            self.event_rows[event.name] = EventData(event.name, event_group, short_name, original_event_name)
        return self.event_rows[event.name]

    def _adjust_event_for_occupancy(self, event: Event, occupancy: trace_events.Occupancy) -> None:
        event.name = occupancy.name
        event.abbrev = str(occupancy.count)

    def _add_event(self, event: Event, original_event_name: str):
        logging.debug("Add event %s at %d", event.name, event.time)
        group = None
        if (occupancy := self._occupancy.acquired(event)) or (occupancy := self._occupancy.released(event)):
            group = "Occupancy"
            self._adjust_event_for_occupancy(event, occupancy)

        self._get_row(event, original_event_name, group=group).insert(event)

    def _add_transaction_event(self, tx: Transaction, event: Event):
        txid = event.data.get("txid")
        if txid is None:
            return

        logging.debug("Add transaction event %s on %d at %d", event.name, txid, event.time)
        transaction = self.transaction_event_rows.get(txid)
        if transaction is None:
            for spec in self._transaction_specifications:
                if spec.start(event.name):
                    transaction = self.transaction_event_rows[txid] = spec.create_row(tx, event)
                    break

        tx = self.transactions.get(txid)
        while transaction is None and tx is not None and tx.parents:
            for parent in tx.parents:
                if parent in self.transaction_event_rows:
                    transaction = self.transaction_event_rows[parent]
                    break

            tx = self.transactions.get(list(tx.parents)[0])

        if transaction is None:
            return

        transaction.process_event(event)
        if event.name not in ("start_transaction", "end_transaction"):
            transaction.insert(event)

    def insert(self, event: Event, apply_filters: bool = False) -> None:
        assert not self.finalized
        original_event_name = event.name

        # If this event is supposed to be handled by the event-splitter, let it
        # modify and/or filter out the event
        if apply_filters and not self._event_filters.apply(event):
            return

        # Generate an 'abbreviation' for this event and do any necessary
        # modification of its data values
        event.map_and_abbreviate(self.mapper)

        if self._first_time is None:
            self._first_time = event.time
            self._last_time = event.time
            self.max_event_id = event.id
        else:
            self._first_time = min(self._first_time, event.time)
            self._last_time = max(self._last_time, event.time)
            self.max_event_id = max(self.max_event_id, event.id)

        tx = self._account_transaction(event)
        if event.name not in ("start_transaction", "end_transaction"):
            self._event_count += 1
            if DataView.RESOURCE in self.views:
                self._add_event(event, original_event_name)
            if DataView.TRANSACTIONS in self.views:
                self._add_transaction_event(tx, event)

    def ancestors(self, event_or_txn: Event | Transaction | int) -> Iterable[Transaction]:
        # Find the txid from whatever object is passed in
        if isinstance(event_or_txn, Event):
            if "txid" in event_or_txn.data:
                txid = event_or_txn.data["txid"]
            else:
                return []
        elif isinstance(event_or_txn, Transaction):
            txid = event_or_txn.txid
        elif isinstance(event_or_txn, int):
            txid = event_or_txn

        assert txid in self.transactions
        to_process = self.transactions[txid].parents.copy()
        processed = set()
        while len(to_process) > 0:
            txid = to_process.pop()
            if txid in self.transactions:
                processed.add(txid)
                to_process |= self.transactions[txid].parents - processed
        return [self.transactions[txid] for txid in processed]

    def descendants(self, event_or_txn: Event | Transaction | int) -> Iterable[Transaction]:
        # Find the txid from whatever object is passed in
        if isinstance(event_or_txn, Event):
            if "txid" in event_or_txn.data:
                txid = event_or_txn.data["txid"]
            else:
                return []
        elif isinstance(event_or_txn, Transaction):
            txid = event_or_txn.txid
        elif isinstance(event_or_txn, int):
            txid = event_or_txn

        assert txid in self.transactions
        to_process = self.transactions[txid].children.copy()
        processed = set()
        while len(to_process) > 0:
            txid = to_process.pop()
            if txid in self.transactions:
                processed.add(txid)
                to_process |= self.transactions[txid].children - processed
        return [self.transactions[txid] for txid in processed]

    def __iter__(self) -> Iterator[EventData]:
        return iter(self.events())

    def events(self, **kwargs: Any) -> "EventStreamDataEventView":
        return EventStreamDataEventView(self, **kwargs)

    def transaction_events(self, **kwargs: Any) -> "EventStreamDataTransactionView":
        return EventStreamDataTransactionView(self, **kwargs)


class EventStreamDataView:
    def __init__(self, esd: EventStreamData, keys: list):
        self.esd = esd
        self._keys = keys

    def __getattr__(self, name: str) -> Any:
        return getattr(self.esd, name)

    def keys(self) -> list:
        return self._keys

    def values(self) -> Iterator:
        return (self.get(key) for key in self._keys)

    class Iter:
        def __init__(self, view: "EventStreamDataView"):
            self._view = view
            self._next_index = 0

        def __iter__(self) -> "EventStreamDataView.Iter":
            return self

        def __next__(self) -> EventData:
            if self._next_index < len(self._view._keys):
                next_key = self._view._keys[self._next_index]
                self._next_index += 1
                return self._view.get(next_key)
            raise StopIteration()

    def __iter__(self) -> Iter:
        return self.Iter(self)

    def __len__(self) -> int:
        return len(self._keys)

    def within_indicies(self, start: int, stop: int) -> Iterator:
        return islice(self.values(), start, stop)


class EventStreamDataEventView(EventStreamDataView):
    """View of events within EventStreamData."""

    def __init__(self, esd: EventStreamData, keys: list | None = None):
        super().__init__(esd, esd.event_row_keys if keys is None else keys)

    @property
    def _all_keys(self) -> bool:
        return self._keys == self.esd.event_row_keys

    def values(self) -> Iterator[EventData]:
        if self._all_keys:
            return self.esd.event_rows.values()
        return (self.esd.event_rows[key] for key in self._keys)

    def get(self, key: str) -> EventData:
        return self.esd.event_rows[key]

    def key_of(self, event: Event) -> str:
        return event.name

    def name_of(self, key: str) -> str:
        return key


class EventStreamDataTransactionView(EventStreamDataView):
    """View of transaction events within EventStreamData."""

    def __init__(self, esd: EventStreamData, keys: list | None = None):
        super().__init__(esd, list(esd.transaction_event_rows.keys()) if keys is None else keys)

    @property
    def _all_keys(self) -> bool:
        return self._keys == self.esd.transaction_event_rows

    def values(self) -> Iterator[TransactionEventData]:
        if self._all_keys:
            return self.transaction_event_rows.values()
        return (self.esd.transaction_event_rows[key] for key in self._keys)

    def get(self, key: int) -> TransactionEventData:
        return self.esd.transaction_event_rows[key]

    def key_of(self, event: Event) -> Any:
        return event.data.get("txid")

    def name_of(self, key: int) -> str:
        return self.get(key).name


class FilePctReporter(IOBase):
    """
    Act like a normal "file" object, but call pct_loaded_callback(percentage)
    periodically with approximately how far through the file the user is (via
    `tell()`). This is useful for reporting how much longer it'll take to load
    a file.
    """

    def __init__(self, filename: str, pct_loaded_callback: Callable):
        self._filename = filename
        self._file_size = os.path.getsize(filename)
        self._pct_loaded = 0
        self._pct_loaded_callback = pct_loaded_callback
        self._pct_num_calls = 0
        self._updates_per_check = 2
        self._consec_without_update = 0

    def __enter__(self) -> "FilePctReporter":
        self._file = open(self._filename, "rb").__enter__()
        return self

    def __exit__(self, exc_type, exc_value, traceback):  # noqa: ANN001, ANN204
        return self._file.__exit__(exc_type, exc_value, traceback)

    def __getattr__(self, name: str) -> Any:
        assert "read" not in name, "Failed to hook `read`-related method of FilePctReporter"
        return getattr(self._file, name)

    def _check_pct(self):
        self._pct_num_calls += 1
        if self._pct_num_calls % self._updates_per_check == 0:
            latest_pct = round(self._file.tell() * 100.0 / self._file_size)
            if latest_pct > self._pct_loaded:
                self._pct_loaded = latest_pct
                self._pct_loaded_callback(self._pct_loaded)
            else:
                self._consec_without_update += 1
                if self._consec_without_update > 1:
                    self._updates_per_check *= 2
                    self._consec_without_update = 0

    def read(self, *args: Any, **kwargs: Any) -> bytes:
        self._check_pct()
        return self._file.read(*args, **kwargs)

    def readinto(self, *args: Any, **kwargs: Any) -> int:
        self._check_pct()
        return self._file.readinto(*args, **kwargs)

    def readline(self, *args: Any, **kwargs: Any) -> bytes:
        self._check_pct()
        return self._file.readline(*args, **kwargs)


def summary_histogram(event_list: list[Event], field: str | None = None) -> Counter:
    histogram = Counter()
    for event in event_list:
        name = event.data.get(field) if field else event.abbrev
        if name:
            histogram[name] += 1
    return histogram


def get_event_data(
    filename: str,
    view: str,
    *,
    mapper: Mapper,
    event_filters: trace_events.EventFilters,
    events: list[EventSpecification],
    post_to_tx: list[str],
    pull_from_tx: list[str],
    occupancy: list[str],
    pct_loaded_callback: Callable,
    convert_enumerations: bool = True,
    **view_options: Any,
) -> EventStreamData:
    esd = EventStreamData(
        filename,
        mapper=mapper,
        event_filters=event_filters,
        events=events,
        post_to_tx=post_to_tx,
        pull_from_tx=pull_from_tx,
        occupancy=occupancy,
    )

    def event_already_in(event: str, event_specs: Iterable[EventSpecification]) -> bool:
        return any(spec(event) for spec in event_specs)

    extra = []
    if view == "transactions":
        extra = esd.add_transaction_view(
            view_options["transaction_name"],
            end=view_options["transaction_end"],
            start=view_options["transaction_start"],
        )
    extra.extend(esd._occupancy.events)

    for e in ["start_transaction", "end_transaction"] + extra:
        if isinstance(e, EventSpecification):
            events.append(e)
        elif not event_already_in(e, events):
            events.append(trace_events.trace_spec(e))

    events += event_filters.events()

    with FilePctReporter(filename, pct_loaded_callback) as file:

        def add_events(es: EventStreamReader):
            for event in es.read_events(events, constructor=CatscanEvent.from_event):
                esd.insert(event, apply_filters=True)

        if filename.endswith("es.xz"):
            with lzma.open(file, "rb") as lzma_file:
                add_events(EventStreamReader(lzma_file, all_events=True, convert_enumerations=convert_enumerations))
        elif filename.endswith("es.gz"):
            with gzip.GzipFile(fileobj=file, mode="rb") as gzip_file:
                add_events(EventStreamReader(gzip_file, all_events=True))
        elif filename.endswith(".es"):
            add_events(EventStreamReader(file, all_events=True, convert_enumerations=convert_enumerations))
        elif filename.endswith(".pickle.gz"):
            with gzip.GzipFile(fileobj=file) as pickle_file:
                return pickle.load(pickle_file)
        elif filename.endswith(".pickle.xz"):
            with lzma.open(file, "rb") as pickle_file:
                return pickle.load(pickle_file)
        else:
            raise Exception(f"Unable to load unexpected file type: {filename}")

    # Finalize it, signifying we're done adding events
    esd.finalize()

    return esd


def save_event_data(filename: str, esd: EventStreamData) -> None:
    if filename.endswith("pickle.xz"):
        with lzma.open(filename, "wb") as pickle_file:
            pickle.dump(esd, pickle_file)
    else:
        if not filename.endswith(".pickle.gz"):
            filename = f"{filename}.pickle.gz"
        with gzip.open(filename, "wb") as pickle_file:
            pickle.dump(esd, pickle_file)
