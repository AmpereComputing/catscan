# Copyright (c) 2024 Ampere Computing. All rights reserved.
# SPDX-License-Identifier: BSD-3-Clause

import asyncio
import atexit
import json
import logging
import os
import time
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass, field
from fractions import Fraction
from threading import Thread
from typing import Literal, NamedTuple

import urwid
from perf_streams.event_stream import Event

from catscan.data import CatscanEvent, DataView, EventData

JsonScalar = str | int | float | bool | None
JsonValue = JsonScalar | list["JsonValue"] | dict[str, "JsonValue"]


def build_commit_index(event_row: EventData, data_name: str) -> dict[int, int]:
    index = {}
    for event in event_row[:]:
        if data_name in event.data:
            index[event.data[data_name]] = event.time
    return index


def build_pushout_index(commit_index: dict[int, int], ps_per_cycle: int) -> dict[int, int]:
    pushout_index = {}
    for sync_index, commit_time in commit_index.items():
        if sync_index - 1 in commit_index:
            pushout_index[sync_index] = (commit_time - commit_index[sync_index - 1]) // ps_per_cycle
    return pushout_index


# This "constant" defines the bounds used to "debounce" changes in cumulative
# commit pushout. The logic in CommitSyncer.generate_commit_pushout_events
# below keeps track of the cumulative commit pushout, and tracks it with a
# value that is the "debounced" cumulative commit pushout. Whenever the real
# cumulative pushout becomes farther than CUMULATIVE_PUSHOUT_BOUNDS cycles from
# the 'debounced' cumulative pushout, two things happen:
#  1) "debounced_cumulative_pushout" events are emitted for the number of
#     cycles of difference between the bounds around the debounced value and
#     the real value, and
#  2) The debounced cumulative pushout value is updated by the same amount
CUMULATIVE_PUSHOUT_BOUNDS = 16

# Refresh rate (seconds) for syncing, allows for dropping older updates to get the most
# recent, reducing rubberbanding
REFRESH_RATE = 1 / 60  # 60Hz


class PushoutEvent(CatscanEvent):
    def __init__(
        self, name: str, _id: int, time: int, txid: int, sync_index: int, my_pushout: int, other_pushout: int
    ) -> None:
        self.id = _id
        self.time = time
        self.name = name
        self.data = {
            "txid": txid,
            "sync_index": sync_index,
            "pushout": my_pushout,
            "other_pushout": other_pushout,
            "excess_pushout": my_pushout - other_pushout,
        }


class CommitSyncState(NamedTuple):
    sync_index: int | None  # The sync_index whose position we are syncing on, if one is available
    cycles_per_char: Fraction  # The number of cycles summarized per character in the sender's current view (directly from its CatscanState)
    expand_rows: bool  # Whether the rows of events should be displayed in their 'expanded' form
    chars_rel_to_start: int | None = None  # The number of characters right-of-center the referenced sync_index is
    movement_alignment: Literal["before", "after"] | None = None


@dataclass
class CommitSyncPeer:
    suffix: str
    init_filename: str
    commit_index: dict[int, int] = field(default_factory=dict)
    pushout_index: dict[int, int] = field(default_factory=dict)
    view_mode: DataView = DataView.RESOURCE
    column_header_width: int = 0


class CommitSyncStateJSONEncoder(json.JSONEncoder):
    def default(self, obj: Fraction) -> JsonValue:
        if isinstance(obj, Fraction):
            return {"numerator": obj.numerator, "denominator": obj.denominator}
        return super().default(obj)

    def encode(self, obj: CommitSyncState | JsonValue) -> str:
        if isinstance(obj, CommitSyncState):
            data = obj._asdict()
            if obj.movement_alignment is not None:
                data["transaction_row_align"] = {
                    "before": "top",
                    "after": "bottom",
                }[obj.movement_alignment]
            return super().encode(data)
        return super().encode(obj)


class CommitSyncStateJSONDecoder(json.JSONDecoder):
    def decode(self, json_string: str) -> CommitSyncState:
        data = super().decode(json_string)
        movement_alignment = data.get("movement_alignment")
        if movement_alignment is None:
            movement_alignment = {
                "top": "before",
                "bottom": "after",
            }.get(data.get("transaction_row_align"))
        return CommitSyncState(
            sync_index=data.get("sync_index"),
            cycles_per_char=Fraction(data["cycles_per_char"]["numerator"], data["cycles_per_char"]["denominator"]),
            expand_rows=data["expand_rows"],
            chars_rel_to_start=data.get("chars_rel_to_start"),
            movement_alignment=movement_alignment,
        )


class CommitSyncer:
    def __init__(
        self,
        fifo_basename: str,
        sync_started_callback: Callable[[], None],
        sync_stopped_callback: Callable[[], None],
        sync_callback: Callable[[CommitSyncState], None],
        column_header_width: int = 0,
        commit_index: dict[int, int] | None = None,
        pushout_index: dict[int, int] | None = None,
        view_mode: DataView = DataView.RESOURCE,
    ) -> None:
        self.fifo_basename = fifo_basename
        self.sync_started_callback = sync_started_callback
        self.sync_stopped_callback = sync_stopped_callback
        self.sync_callback = sync_callback

        # Create the FIFOs themselves, assuming we are the secondary unless we
        # were able to create FIFO
        self.fifo1_name = os.path.abspath(f"{fifo_basename}.fifo.1")
        self.fifo2_name = os.path.abspath(f"{fifo_basename}.fifo.2")
        self.is_primary = self.make_fifo(self.fifo1_name)
        self.make_fifo(self.fifo2_name)

        my_suffix = "primary" if self.is_primary else "secondary"
        other_suffix = "secondary" if self.is_primary else "primary"
        self.my = CommitSyncPeer(
            suffix=my_suffix,
            init_filename=os.path.abspath(f"{fifo_basename}.init.{my_suffix}"),
            commit_index=commit_index or {},
            pushout_index=pushout_index or {},
            view_mode=view_mode,
            column_header_width=column_header_width,
        )
        self.other = CommitSyncPeer(
            suffix=other_suffix,
            init_filename=os.path.abspath(f"{fifo_basename}.init.{other_suffix}"),
        )
        self.failure_message = None

        self.state_encoder = CommitSyncStateJSONEncoder()
        self.state_decoder = CommitSyncStateJSONDecoder()

        self.outgoing = None
        self.incoming = None
        self.initialized = False
        self.stopped = False
        self.initialization_thread = None

        # Event-loop related attributes
        self.main_loop = None
        self.event_loop = None
        self.future_sync = None
        self.notifier = None
        self.latest_sync_state = None

    def cleanup_file(self, filename: str) -> None:
        with suppress(FileNotFoundError):
            os.unlink(filename)

    def make_fifo(self, filename: str) -> bool | None:
        try:
            os.mkfifo(filename)
        except FileExistsError:
            return False

        # Clean up the FIFO at exit if we just created it
        atexit.register(self.cleanup_file, filename)

        return True

    def start(self, main_loop: urwid.MainLoop | None) -> None:
        """Start commit syncing.

        Args:
            main_loop (urwid.MainLoop | None): if a main-loop is provided,
            syncing will be maintained by an asyncio, otherwise the legacy
            Threading will be used

        """
        if main_loop is None:
            # Use threading
            self.initialization_thread = Thread(target=self._start)
            self.initialization_thread.start()
        else:
            # Notifier runs on main thread/loop as alarms are not thread-safe and must
            # be called from within main-loop thread
            def notify(_data: bytes) -> bool:
                if self.latest_sync_state is not None:
                    if self.future_sync:
                        self.main_loop.remove_alarm(self.future_sync)
                        if self.future_sync.when() > self.event_loop.time():
                            # Reschedule update with new data
                            self.future_sync = self.main_loop.set_alarm_at(
                                self.future_sync.when(),
                                lambda _loop, state: self.sync_callback(state),
                                self.latest_sync_state,
                            )
                        else:
                            self.future_sync = None

                    if self.future_sync is None:
                        # Schedule new update
                        self.future_sync = self.main_loop.set_alarm_in(
                            REFRESH_RATE - (self.event_loop.time() % REFRESH_RATE),
                            lambda _loop, state: self.sync_callback(state),
                            self.latest_sync_state,
                        )

                return not self.stopped

            # Use urwid event-loop
            self.main_loop = main_loop
            self.event_loop = asyncio.get_event_loop()
            # Required for initialization_thread to call functions within main-loop safely
            self.notifier = self.main_loop.watch_pipe(notify)
            # Run in executor as it's blocking IO
            self.initialization_thread = self.main_loop.event_loop.run_in_executor(None, self._start)

    def _start(self) -> None:
        """
        Note: Should only be called inside the thread saved as self.initialization_thread.
        """

        atexit.register(self.cleanup_file, self.my.init_filename)
        with open(self.my.init_filename, "w") as init_file:
            to_json = {
                "column_header_width": self.my.column_header_width,
                "commit_index": self.my.commit_index,
                "pushout_index": self.my.pushout_index,
                "view_mode": self.my.view_mode,
            }
            json.dump(to_json, init_file)
            init_file.flush()

        # Note: Both sides must open fifo1 first, because the calls to open the
        # FIFOs block until the other end is opened so you'll run into a deadlock
        # otherwise
        if self.is_primary:
            self.outgoing = open(self.fifo1_name, "w")
            self.incoming = open(self.fifo2_name)
        else:
            self.incoming = open(self.fifo1_name)
            self.outgoing = open(self.fifo2_name, "w")

        while not self.stopped:
            try:
                with open(self.other.init_filename) as init_file:
                    from_json = json.load(init_file)
                    self.other.column_header_width = from_json["column_header_width"]
                    self.other.commit_index = {
                        int(sync_index): time for sync_index, time in from_json.get("commit_index", {}).items()
                    }
                    self.other.pushout_index = {
                        int(sync_index): pushout for sync_index, pushout in from_json["pushout_index"].items()
                    }
                    if "view_mode" in from_json:
                        self.other.view_mode = DataView(from_json["view_mode"])
                    else:
                        self.other.view_mode = DataView.RESOURCE
                break
            except FileNotFoundError:
                time.sleep(0.05)
        else:
            return

        # Signify that initialization is complete to any observers waiting on
        # that
        self.initialized = True
        self.sync_started_callback()

        while not self.stopped:
            data = self.incoming.readline()
            if len(data) and not self.stopped:
                sync_state = self.state_decoder.decode(data.strip())
                self.receive(sync_state)
            else:
                logging.info("Stopping commit sync. Other end of FIFO presumed closed.")
                self.stop()

    def generate_commit_pushout_events(
        self,
        event_row: EventData,
        commit_sync_data_name: str,
        next_event_id: int,
        ps_per_cycle: int,
    ) -> list[Event]:
        # Generate a dictionary keyed by sync_index for each commit which shows
        # "real" excess commit pushout. We don't count commit pushout as "real"
        # if it is a single cycle and the previous nonzero pushout was -1
        # cycles, because this likely means the commits are just split across
        # cycles slightly differently.
        min_sync_index = max(min(self.my.pushout_index.keys()), min(self.other.pushout_index.keys()))
        max_sync_index = min(max(self.my.pushout_index.keys()), max(self.other.pushout_index.keys()))

        excess_pushout = {}
        cumulative_pushout_movement = {}
        last_pushout_difference = 0
        cumulative_pushout = 0
        cumulative_pushout_center = cumulative_pushout
        for sync_index in range(min_sync_index, max_sync_index + 1):
            try:
                diff = self.my.pushout_index[sync_index] - self.other.pushout_index[sync_index]
            except KeyError:
                continue

            if diff > 1 or (diff == 1 and last_pushout_difference != -1):
                excess_pushout[sync_index] = diff
            if diff:
                cumulative_pushout += diff
                last_pushout_difference = diff
                if cumulative_pushout < cumulative_pushout_center - CUMULATIVE_PUSHOUT_BOUNDS:
                    cumulative_pushout_center = cumulative_pushout + CUMULATIVE_PUSHOUT_BOUNDS
                elif cumulative_pushout > cumulative_pushout_center + CUMULATIVE_PUSHOUT_BOUNDS:
                    cumulative_pushout_movement[sync_index] = cumulative_pushout - (
                        cumulative_pushout_center + CUMULATIVE_PUSHOUT_BOUNDS
                    )
                    cumulative_pushout_center = cumulative_pushout - CUMULATIVE_PUSHOUT_BOUNDS

        pushout_events = []

        def gen_pushout_events(commit_evt: Event, event_name: str, excess_pushout: int) -> None:
            nonlocal next_event_id
            for i in range(-excess_pushout, 0):
                time = commit_evt.time + i * ps_per_cycle
                pushout_evt = PushoutEvent(
                    event_name,
                    next_event_id,
                    time,
                    commit_evt.data["txid"],
                    commit_evt.data[commit_sync_data_name],
                    self.my.pushout_index[sync_index],
                    self.other.pushout_index[sync_index],
                )
                next_event_id += 1
                pushout_events.append(pushout_evt)

        group_prefix = event_row.group
        for commit_evt in event_row[:]:
            if commit_sync_data_name not in commit_evt.data:
                continue
            sync_index = commit_evt.data[commit_sync_data_name]
            if sync_index in excess_pushout:
                gen_pushout_events(commit_evt, f"{group_prefix}.excess_commit_pushout", excess_pushout[sync_index])
            if sync_index in cumulative_pushout_movement:
                gen_pushout_events(
                    commit_evt, f"{group_prefix}.debounced_cumulative_pushout", cumulative_pushout_movement[sync_index]
                )

        return pushout_events

    def stop(self) -> None:
        if self.stopped:
            return

        # Note that we notify observers prior to actually closing/cleaning
        # anything up in an attempt to avoid race conditions, though we don't
        # actually introduce barriers/locks here.
        self.stopped = True
        self.sync_stopped_callback()

        self.cleanup_file(self.fifo1_name)
        self.cleanup_file(self.fifo2_name)
        if self.initialized or self.failure_message is None:
            self.cleanup_file(self.my.init_filename)
        if self.outgoing:
            self.outgoing.close()
            self.outgoing = None
        if self.incoming:
            self.incoming.close()
            self.incoming = None

        if self.main_loop is not None and self.initialization_thread is not None:
            self.initialization_thread.cancel()
            self.initialization_thread = None

        if self.notifier:
            if self.main_loop.remove_watch_pipe(self.notifier):
                os.close(self.notifier)
            self.notifier = None

    @property
    def syncing(self) -> bool:
        return bool(self.initialized and not self.stopped and self.outgoing)

    def send(self, sync_state: CommitSyncState) -> None:
        if not self.syncing:
            logging.warning(
                f"Dropping to-send commit sync message {sync_state} because the sync is either not initialized yet or has been closed/stopped."
            )
            return

        sync_index_json = self.state_encoder.encode(sync_state)
        self.outgoing.write(f"{sync_index_json}\n")
        self.outgoing.flush()

    def receive(self, sync_state: CommitSyncState) -> None:
        if not self.syncing:
            logging.warning(
                f"Dropping received commit sync message {sync_state} because the sync is either not initialized yet or has been closed/stopped."
            )
            return

        if (
            self.other.view_mode == DataView.TRANSACTIONS
            or sync_state.sync_index is None
            or sync_state.chars_rel_to_start is None
        ):
            adjusted_sync_state = sync_state
            if self.main_loop is None:
                self.sync_callback(adjusted_sync_state)
            else:
                self.latest_sync_state = adjusted_sync_state
                # Required to wakeup/interrupt main loop
                os.write(self.notifier, b"u")
            return

        # Scale the synced horizontal time based on the relative differences in
        # commit pushout to ensure smoother movements around transitions
        # between the sync_index being synced against
        if sync_state.chars_rel_to_start < 0 and sync_state.sync_index in self.my.pushout_index:
            scalable_rel_chars = max(sync_state.chars_rel_to_start, -self.other.pushout_index[sync_state.sync_index])
            rel_chars = sync_state.chars_rel_to_start - scalable_rel_chars
            if scalable_rel_chars:
                rel_chars += (
                    scalable_rel_chars
                    * self.my.pushout_index[sync_state.sync_index]
                    / self.other.pushout_index[sync_state.sync_index]
                )
        elif (
            sync_state.chars_rel_to_start > 0
            and sync_state.sync_index + 1 in self.my.pushout_index
            and sync_state.sync_index + 1 in self.other.pushout_index
        ):
            scalable_rel_chars = min(sync_state.chars_rel_to_start, self.other.pushout_index[sync_state.sync_index + 1])
            rel_chars = sync_state.chars_rel_to_start - scalable_rel_chars
            if scalable_rel_chars:
                rel_chars += (
                    scalable_rel_chars
                    * self.my.pushout_index[sync_state.sync_index + 1]
                    / self.other.pushout_index[sync_state.sync_index + 1]
                )
        else:
            rel_chars = sync_state.chars_rel_to_start

        adjusted_sync_state = CommitSyncState(
            sync_index=sync_state.sync_index,
            cycles_per_char=sync_state.cycles_per_char,
            expand_rows=sync_state.expand_rows,
            chars_rel_to_start=rel_chars,
            movement_alignment=sync_state.movement_alignment,
        )

        if self.main_loop is None:
            self.sync_callback(adjusted_sync_state)
        else:
            self.latest_sync_state = adjusted_sync_state
            # Required to wakeup/interrupt main loop
            os.write(self.notifier, b"u")
