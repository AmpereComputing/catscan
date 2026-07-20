# Copyright (c) 2024-2025 Ampere Computing. All rights reserved.
# SPDX-License-Identifier: BSD-3-Clause

import io
import json
import os
import tempfile
import time
import unittest
from fractions import Fraction

from catscan.commit_sync import *
from catscan.data import DataView


class TestCommitSync(unittest.TestCase):
    def initialized_first(self):
        self.first_initialized = True

    def initialized_second(self):
        self.second_initialized = True

    def stopped_first(self):
        self.first_stopped = True

    def stopped_second(self):
        self.second_stopped = True

    def sync_first(self, sync_state: CommitSyncState) -> None:
        self.first_incoming_messages.append(sync_state)

    def sync_second(self, sync_state: CommitSyncState) -> None:
        self.second_incoming_messages.append(sync_state)

    def wait_for_initialization(self, wait_seconds=5):
        for _ in range(wait_seconds * 2):
            if self.first_initialized and self.second_initialized:
                break
            time.sleep(0.5)

        self.assertTrue(self.first_syncer.initialized)
        self.assertTrue(self.second_syncer.initialized)

    def wait_for_stop(self, wait_seconds=5):
        for _ in range(wait_seconds * 2):
            if self.first_stopped and self.second_stopped:
                break
            time.sleep(0.5)

        self.assertTrue(self.first_syncer.stopped)
        self.assertTrue(self.second_syncer.stopped)

    def wait_for_messages(self, first_messages=1, second_messages=1, wait_seconds=5):
        for _ in range(wait_seconds * 2):
            if (
                len(self.first_incoming_messages) >= first_messages
                and len(self.second_incoming_messages) >= second_messages
            ):
                break
            time.sleep(0.5)

        self.assertGreaterEqual(len(self.first_incoming_messages), first_messages)
        self.assertGreaterEqual(len(self.second_incoming_messages), second_messages)

    def setUp(self):
        # The next four variables are set/modified by callbacks
        self.first_initialized = False
        self.second_initialized = False
        self.first_stopped = False
        self.second_stopped = False
        self.first_incoming_messages = []
        self.second_incoming_messages = []

        with tempfile.NamedTemporaryFile() as tmpfile:
            # Note: the tempfile's name is used as a "basename" here, and it is
            # expected that the actual file here is deleted by the context
            # manager at the end of this block
            self.first_syncer = CommitSyncer(tmpfile.name, self.initialized_first, self.stopped_first, self.sync_first)
            self.first_syncer.start(None)
            self.second_syncer = CommitSyncer(
                tmpfile.name, self.initialized_second, self.stopped_second, self.sync_second
            )
            self.second_syncer.start(None)

        self.wait_for_initialization()

    def tearDown(self):
        if self.first_syncer.initialized:
            self.first_syncer.stop()
        if self.second_syncer.initialized:
            self.second_syncer.stop()

        self.assertTrue(self.first_syncer.stopped)
        self.assertTrue(self.second_syncer.stopped)

    def test_initialization_callbacks(self):
        self.assertTrue(self.first_initialized)
        self.assertTrue(self.second_initialized)

    def test_send_receive_messages(self):
        self.first_syncer.send(
            CommitSyncState(sync_index=42, cycles_per_char=Fraction(1, 16), expand_rows=False, chars_rel_to_start=-47)
        )

        # Alternate sending a few sync messages each direction
        for i in range(3):
            self.first_syncer.send(
                CommitSyncState(
                    sync_index=i, cycles_per_char=Fraction(2**i), expand_rows=True, chars_rel_to_start=10 - i
                )
            )
            self.second_syncer.send(
                CommitSyncState(
                    sync_index=i * 3, cycles_per_char=Fraction(8**i), expand_rows=True, chars_rel_to_start=30 + i
                )
            )
            self.wait_for_messages(first_messages=i + 1, second_messages=i + 2)

        # Make sure the sync indexes received match what was sent
        self.assertEqual([m.sync_index for m in self.first_incoming_messages], [0, 3, 6])
        self.assertEqual([m.sync_index for m in self.second_incoming_messages], [42, 0, 1, 2])

        # And the other fields, too
        self.assertEqual(self.second_incoming_messages[0].cycles_per_char, Fraction(1, 16))
        self.assertEqual(self.second_incoming_messages[0].expand_rows, False)
        self.assertEqual(self.second_incoming_messages[0].chars_rel_to_start, -47)
        self.assertEqual(self.second_incoming_messages[1].expand_rows, True)

    def test_stop(self):
        self.first_syncer.send(
            CommitSyncState(sync_index=42, cycles_per_char=Fraction(1, 16), expand_rows=True, chars_rel_to_start=-47)
        )

        self.first_syncer.stop()
        self.wait_for_stop()

        # This message should be dropped after logging the stopped sync.
        with self.assertLogs(level="WARNING") as logs:
            self.second_syncer.send(
                CommitSyncState(
                    sync_index=49, cycles_per_char=Fraction(32, 1), expand_rows=True, chars_rel_to_start=109
                )
            )
        self.assertIn("Dropping to-send commit sync message", logs.output[0])

        self.assertTrue(self.first_stopped)
        self.assertTrue(self.second_stopped)

        self.wait_for_messages(first_messages=0, second_messages=1)
        self.assertEqual(self.second_incoming_messages[0].sync_index, 42)

        # Ensure we never received the message sent after the FIFO was stopped
        time.sleep(2)
        self.assertEqual(len(self.first_incoming_messages), 0)

    def test_state_json_round_trip_supports_optional_anchor(self):
        encoder = CommitSyncStateJSONEncoder()
        decoder = CommitSyncStateJSONDecoder()

        state = CommitSyncState(
            sync_index=None,
            cycles_per_char=Fraction(3, 2),
            expand_rows=True,
            chars_rel_to_start=None,
            movement_alignment="after",
        )
        encoded = encoder.encode(state)
        decoded = decoder.decode(encoded)
        encoded_state = json.loads(encoded)

        self.assertEqual(decoded, state)
        self.assertNotIn("sync_index", encoded_state)
        self.assertNotIn("chars_rel_to_start", encoded_state)
        self.assertEqual(encoded_state["movement_alignment"], "after")

    def test_state_json_decode_accepts_current_payload(self):
        decoder = CommitSyncStateJSONDecoder()

        state = decoder.decode(
            '{"sync_index": 9, "cycles_per_char": {"numerator": 5, "denominator": 4}, '
            '"expand_rows": false, "chars_rel_to_start": 11}'
        )

        self.assertIsNone(state.movement_alignment)
        self.assertEqual(state.sync_index, 9)
        self.assertEqual(state.cycles_per_char, Fraction(5, 4))

    def test_state_json_decode_accepts_partial_payload(self):
        decoder = CommitSyncStateJSONDecoder()

        state = decoder.decode('{"expand_rows": true}')

        self.assertIsNone(state.sync_index)
        self.assertIsNone(state.cycles_per_char)
        self.assertTrue(state.expand_rows)
        self.assertIsNone(state.chars_rel_to_start)
        self.assertIsNone(state.movement_alignment)

    def test_state_json_decode_ignores_legacy_mode(self):
        decoder = CommitSyncStateJSONDecoder()

        state = decoder.decode(
            '{"sync_index": 9, "cycles_per_char": {"numerator": 5, "denominator": 4}, '
            '"expand_rows": false, "chars_rel_to_start": 11, "mode": "transaction_row"}'
        )

        self.assertIsNone(state.movement_alignment)
        self.assertEqual(state.sync_index, 9)

    def test_state_merge_overlays_non_none_delta_fields(self):
        state = CommitSyncState(
            sync_index=10,
            cycles_per_char=Fraction(1, 1),
            expand_rows=False,
            chars_rel_to_start=7,
            movement_alignment="after",
        )

        merged = state.merge(
            CommitSyncState(
                cycles_per_char=Fraction(1, 2),
                expand_rows=True,
            )
        )

        self.assertEqual(
            merged,
            CommitSyncState(
                sync_index=10,
                cycles_per_char=Fraction(1, 2),
                expand_rows=True,
                chars_rel_to_start=7,
                movement_alignment="after",
            ),
        )

    def test_state_has_fields_detects_non_none_values(self):
        self.assertFalse(CommitSyncState().has_fields())
        self.assertTrue(CommitSyncState(expand_rows=False).has_fields())

    def test_transaction_row_receive_bypasses_pushout_scaling(self):
        received = []
        with tempfile.NamedTemporaryFile() as tmpfile:
            syncer = CommitSyncer(tmpfile.name, lambda: None, lambda: None, received.append)

        syncer.initialized = True
        syncer.stopped = False
        syncer.outgoing = object()
        syncer.my.pushout_index = {10: 8, 11: 16}
        syncer.other.pushout_index = {10: 2, 11: 4}
        syncer.other.view_mode = DataView.TRANSACTIONS

        sync_state = CommitSyncState(
            sync_index=10,
            cycles_per_char=Fraction(1, 1),
            expand_rows=False,
            chars_rel_to_start=-9,
        )
        syncer.receive(sync_state)

        self.assertEqual(received, [sync_state])
        syncer.outgoing = None
        syncer.stop()

    def test_receive_keeps_negative_offset_without_peer_pushout(self):
        received = []
        with tempfile.NamedTemporaryFile() as tmpfile:
            syncer = CommitSyncer(tmpfile.name, lambda: None, lambda: None, received.append)

        syncer.initialized = True
        syncer.stopped = False
        syncer.outgoing = object()
        syncer.my.pushout_index = {10: 8}
        syncer.other.commit_index = {10: 100}
        syncer.other.pushout_index = {}

        sync_state = CommitSyncState(
            sync_index=10,
            cycles_per_char=Fraction(1, 1),
            expand_rows=False,
            chars_rel_to_start=-1,
        )
        syncer.receive(sync_state)

        self.assertEqual(received, [sync_state])
        syncer.outgoing = None
        syncer.stop()

    def test_receive_display_only_delta_merges_with_latest_state(self):
        received = []
        with tempfile.NamedTemporaryFile() as tmpfile:
            syncer = CommitSyncer(tmpfile.name, lambda: None, lambda: None, received.append)

        syncer.initialized = True
        syncer.stopped = False
        syncer.outgoing = object()
        self.addCleanup(lambda: self.cleanup_inert_syncer(syncer))

        full_sync_state = CommitSyncState(
            sync_index=10,
            cycles_per_char=Fraction(1, 1),
            expand_rows=False,
            chars_rel_to_start=7,
            movement_alignment="after",
        )
        display_delta = CommitSyncState(
            cycles_per_char=Fraction(1, 2),
            expand_rows=True,
        )

        syncer.receive(full_sync_state)
        syncer.receive(display_delta)

        merged_sync_state = CommitSyncState(
            sync_index=10,
            cycles_per_char=Fraction(1, 2),
            expand_rows=True,
            chars_rel_to_start=7,
            movement_alignment="after",
        )
        self.assertEqual(received, [full_sync_state, merged_sync_state])
        self.assertEqual(syncer.latest_sync_state, merged_sync_state)

    def test_main_loop_dispatch_merges_deltas_before_callback_runs(self):
        received = []
        with tempfile.NamedTemporaryFile() as tmpfile:
            syncer = CommitSyncer(tmpfile.name, lambda: None, lambda: None, received.append)

        read_fd, write_fd = os.pipe()
        self.addCleanup(os.close, read_fd)
        self.addCleanup(os.close, write_fd)
        self.addCleanup(lambda: self.cleanup_inert_syncer(syncer))
        syncer.main_loop = object()
        syncer.notifier = write_fd

        syncer._dispatch_received_sync_state(
            CommitSyncState(
                sync_index=10,
                cycles_per_char=Fraction(1, 1),
                expand_rows=False,
                chars_rel_to_start=7,
                movement_alignment="after",
            )
        )
        syncer._dispatch_received_sync_state(CommitSyncState(expand_rows=True))
        syncer._dispatch_received_sync_state(CommitSyncState(cycles_per_char=Fraction(1, 2)))

        self.assertEqual(received, [])
        self.assertEqual(
            syncer.latest_sync_state,
            CommitSyncState(
                sync_index=10,
                cycles_per_char=Fraction(1, 2),
                expand_rows=True,
                chars_rel_to_start=7,
                movement_alignment="after",
            ),
        )

    def cleanup_inert_syncer(self, syncer):
        syncer.outgoing = None
        syncer.main_loop = None
        syncer.notifier = None
        syncer.stop()


class TestCommitPushoutMovements(unittest.TestCase):
    def make_syncer(self, my_pushout_index, other_pushout_index):
        with tempfile.NamedTemporaryFile() as tmpfile:
            syncer = CommitSyncer(tmpfile.name, lambda: None, lambda: None, lambda _state: None)

        syncer.my.pushout_index = my_pushout_index
        syncer.other.pushout_index = other_pushout_index
        self.addCleanup(syncer.stop)
        return syncer

    def test_excess_pushout_debounces_split_cycle_difference(self):
        syncer = self.make_syncer(
            {
                1: 9,
                2: 8,
                3: 9,
                4: 11,
                5: 10,
            },
            {
                1: 10,
                2: 7,
                3: 8,
                4: 9,
                5: 9,
            },
        )

        movements = syncer.compute_commit_pushout_movements()

        self.assertEqual(movements.excess_pushout, {3: 1, 4: 2, 5: 1})
        self.assertEqual(movements.cumulative_pushout_movement, {})

    def test_cumulative_pushout_movement_tracks_threshold_crossings(self):
        syncer = self.make_syncer(
            {
                1: 10,
                2: 8,
                3: 17,
            },
            {
                1: 0,
                2: 0,
                3: 0,
            },
        )

        movements = syncer.compute_commit_pushout_movements()

        self.assertEqual(movements.excess_pushout, {1: 10, 2: 8, 3: 17})
        self.assertEqual(movements.cumulative_pushout_movement, {2: 2, 3: 17})

    def test_missing_pushout_indexes_produce_no_movements(self):
        syncer = self.make_syncer({}, {1: 1})

        movements = syncer.compute_commit_pushout_movements()

        self.assertEqual(movements.excess_pushout, {})
        self.assertEqual(movements.cumulative_pushout_movement, {})


class TestCommitSyncSendIfChanged(unittest.TestCase):
    def make_syncer(self):
        with tempfile.NamedTemporaryFile() as tmpfile:
            syncer = CommitSyncer(tmpfile.name, lambda: None, lambda: None, lambda _state: None)

        syncer.initialized = True
        syncer.stopped = False
        syncer.outgoing = io.StringIO()
        self.addCleanup(syncer.stop)
        return syncer

    def test_send_if_changed_sends_first_state(self):
        syncer = self.make_syncer()
        sync_state = CommitSyncState(
            sync_index=10,
            cycles_per_char=Fraction(1, 1),
            expand_rows=False,
            chars_rel_to_start=0,
        )

        self.assertTrue(syncer.send_if_changed(sync_state))

        self.assertEqual(len(syncer.outgoing.getvalue().splitlines()), 1)

    def test_send_writes_given_state_without_delta_suppression(self):
        syncer = self.make_syncer()
        sync_state = CommitSyncState(
            sync_index=10,
            cycles_per_char=Fraction(1, 1),
            expand_rows=False,
            chars_rel_to_start=0,
        )

        self.assertTrue(syncer.send(sync_state))
        self.assertTrue(syncer.send(sync_state))
        messages = [json.loads(line) for line in syncer.outgoing.getvalue().splitlines()]

        self.assertEqual(len(messages), 2)
        self.assertEqual(messages[1]["sync_index"], 10)
        self.assertEqual(messages[1]["cycles_per_char"], {"numerator": 1, "denominator": 1})
        self.assertFalse(messages[1]["expand_rows"])
        self.assertEqual(messages[1]["chars_rel_to_start"], 0)

    def test_send_if_changed_suppresses_duplicate_state(self):
        syncer = self.make_syncer()
        sync_state = CommitSyncState(
            sync_index=10,
            cycles_per_char=Fraction(1, 1),
            expand_rows=False,
            chars_rel_to_start=0,
        )

        syncer.send_if_changed(sync_state)
        self.assertFalse(syncer.send_if_changed(sync_state))

        self.assertEqual(len(syncer.outgoing.getvalue().splitlines()), 1)

    def test_send_if_changed_sends_changed_state(self):
        syncer = self.make_syncer()
        first_sync_state = CommitSyncState(
            sync_index=10,
            cycles_per_char=Fraction(1, 1),
            expand_rows=False,
            chars_rel_to_start=0,
        )
        second_sync_state = CommitSyncState(
            sync_index=10,
            cycles_per_char=Fraction(1, 2),
            expand_rows=False,
            chars_rel_to_start=0,
        )

        syncer.send_if_changed(first_sync_state)
        self.assertTrue(syncer.send_if_changed(second_sync_state))

        self.assertEqual(len(syncer.outgoing.getvalue().splitlines()), 2)

    def test_display_only_delta_omits_anchor_fields(self):
        syncer = self.make_syncer()
        anchored_sync_state = CommitSyncState(
            sync_index=10,
            cycles_per_char=Fraction(1, 1),
            expand_rows=False,
            chars_rel_to_start=0,
        )
        display_sync_state = CommitSyncState(
            cycles_per_char=Fraction(1, 2),
            expand_rows=True,
        )

        syncer.send_if_changed(anchored_sync_state)
        self.assertTrue(syncer.send_if_changed(display_sync_state))
        messages = [json.loads(line) for line in syncer.outgoing.getvalue().splitlines()]

        self.assertNotIn("sync_index", messages[1])
        self.assertNotIn("chars_rel_to_start", messages[1])
        self.assertEqual(messages[1]["cycles_per_char"], {"numerator": 1, "denominator": 2})
        self.assertTrue(messages[1]["expand_rows"])

    def test_repeated_anchor_omits_anchor_fields_but_sends_display_changes(self):
        syncer = self.make_syncer()
        anchored_sync_state = CommitSyncState(
            sync_index=10,
            cycles_per_char=Fraction(1, 1),
            expand_rows=False,
            chars_rel_to_start=0,
        )
        changed_display_sync_state = CommitSyncState(
            sync_index=10,
            cycles_per_char=Fraction(1, 2),
            expand_rows=False,
            chars_rel_to_start=0,
        )

        syncer.send_if_changed(anchored_sync_state)
        self.assertTrue(syncer.send_if_changed(changed_display_sync_state))
        messages = [json.loads(line) for line in syncer.outgoing.getvalue().splitlines()]

        self.assertNotIn("sync_index", messages[1])
        self.assertNotIn("chars_rel_to_start", messages[1])
        self.assertEqual(messages[1]["cycles_per_char"], {"numerator": 1, "denominator": 2})
        self.assertNotIn("expand_rows", messages[1])
        self.assertEqual(syncer._last_sent_sync_state, changed_display_sync_state)

    def test_send_if_changed_ignores_none_fields(self):
        syncer = self.make_syncer()
        anchored_sync_state = CommitSyncState(
            sync_index=10,
            cycles_per_char=Fraction(1, 1),
            expand_rows=False,
            chars_rel_to_start=0,
        )
        display_sync_state = CommitSyncState(
            sync_index=None,
            cycles_per_char=Fraction(1, 1),
            expand_rows=False,
            chars_rel_to_start=None,
        )

        syncer.send_if_changed(anchored_sync_state)
        self.assertFalse(syncer.send_if_changed(display_sync_state))

        self.assertEqual(len(syncer.outgoing.getvalue().splitlines()), 1)

    def test_unanchored_state_invalidates_anchor_cache_without_clear_message(self):
        syncer = self.make_syncer()
        anchored_sync_state = CommitSyncState(
            sync_index=10,
            cycles_per_char=Fraction(1, 1),
            expand_rows=False,
            chars_rel_to_start=0,
        )
        unanchored_sync_state = CommitSyncState(
            cycles_per_char=Fraction(1, 1),
            expand_rows=False,
        )

        syncer.send_if_changed(anchored_sync_state)
        self.assertFalse(syncer.send_if_changed(unanchored_sync_state))

        self.assertEqual(len(syncer.outgoing.getvalue().splitlines()), 1)
        self.assertIsNone(syncer._last_sent_anchor_state)

    def test_returning_to_same_anchor_after_unanchored_state_sends_anchor_again(self):
        syncer = self.make_syncer()
        anchored_sync_state = CommitSyncState(
            sync_index=10,
            cycles_per_char=Fraction(1, 1),
            expand_rows=False,
            chars_rel_to_start=0,
        )
        unanchored_sync_state = CommitSyncState(
            cycles_per_char=Fraction(1, 1),
            expand_rows=False,
        )

        syncer.send_if_changed(anchored_sync_state)
        syncer.send_if_changed(unanchored_sync_state)
        self.assertTrue(syncer.send_if_changed(anchored_sync_state))
        messages = [json.loads(line) for line in syncer.outgoing.getvalue().splitlines()]

        self.assertEqual(len(messages), 2)
        self.assertEqual(messages[1]["sync_index"], 10)
        self.assertEqual(messages[1]["chars_rel_to_start"], 0)

    def test_duplicate_display_only_unanchored_state_is_suppressed(self):
        syncer = self.make_syncer()
        unanchored_sync_state = CommitSyncState(
            cycles_per_char=Fraction(1, 1),
            expand_rows=False,
        )

        self.assertTrue(syncer.send_if_changed(unanchored_sync_state))
        self.assertFalse(syncer.send_if_changed(unanchored_sync_state))
        messages = [json.loads(line) for line in syncer.outgoing.getvalue().splitlines()]

        self.assertEqual(len(messages), 1)
        self.assertNotIn("sync_index", messages[0])
        self.assertNotIn("chars_rel_to_start", messages[0])

    def test_sent_anchor_matches_ignores_display_state(self):
        syncer = self.make_syncer()
        anchored_sync_state = CommitSyncState(
            sync_index=10,
            cycles_per_char=Fraction(1, 1),
            expand_rows=False,
            chars_rel_to_start=0,
        )
        same_anchor_sync_state = CommitSyncState(
            sync_index=10,
            cycles_per_char=Fraction(1, 2),
            expand_rows=True,
            chars_rel_to_start=0,
        )

        syncer.send(anchored_sync_state)

        self.assertTrue(syncer.sent_anchor_matches(same_anchor_sync_state))

    def test_sent_anchor_matches_detects_different_anchor(self):
        syncer = self.make_syncer()
        anchored_sync_state = CommitSyncState(
            sync_index=10,
            cycles_per_char=Fraction(1, 1),
            expand_rows=False,
            chars_rel_to_start=0,
        )
        different_anchor_sync_state = CommitSyncState(
            sync_index=11,
            cycles_per_char=Fraction(1, 1),
            expand_rows=False,
            chars_rel_to_start=0,
        )

        syncer.send(anchored_sync_state)

        self.assertFalse(syncer.sent_anchor_matches(different_anchor_sync_state))

    def test_sent_anchor_matches_detects_different_alignment(self):
        syncer = self.make_syncer()
        anchored_sync_state = CommitSyncState(
            sync_index=10,
            cycles_per_char=Fraction(1, 1),
            expand_rows=False,
            chars_rel_to_start=0,
        )
        different_alignment_sync_state = CommitSyncState(
            sync_index=10,
            cycles_per_char=Fraction(1, 1),
            expand_rows=False,
            chars_rel_to_start=0,
            movement_alignment="after",
        )

        syncer.send(anchored_sync_state)

        self.assertFalse(syncer.sent_anchor_matches(different_alignment_sync_state))

    def test_peer_sync_index_helpers_check_commit_and_pushout_indexes(self):
        syncer = self.make_syncer()
        syncer.other.commit_index = {10: 100}
        syncer.other.pushout_index = {20: 2}

        self.assertTrue(syncer.other_has_sync_index(10))
        self.assertTrue(syncer.other_has_sync_index(20))
        self.assertFalse(syncer.other_has_sync_index(30))
        self.assertEqual(syncer.first_other_sync_index([30, 20, 10]), 20)
        self.assertIsNone(syncer.first_other_sync_index([30, 40]))


class TestCommitSyncHandshake(unittest.TestCase):
    def test_handshake_accepts_mixed_view_modes(self):
        started = []
        stopped = []

        def started_callback(which):
            started.append(which)

        def stopped_callback(which):
            stopped.append(which)

        with tempfile.NamedTemporaryFile() as tmpfile:
            first_syncer = CommitSyncer(
                tmpfile.name,
                lambda: started_callback("first"),
                lambda: stopped_callback("first"),
                lambda _state: None,
                view_mode=DataView.RESOURCE,
            )
            second_syncer = CommitSyncer(
                tmpfile.name,
                lambda: started_callback("second"),
                lambda: stopped_callback("second"),
                lambda _state: None,
                view_mode=DataView.TRANSACTIONS,
            )
            first_syncer.start(None)
            second_syncer.start(None)

            for _ in range(10):
                if first_syncer.initialized and second_syncer.initialized:
                    break
                time.sleep(0.5)

            self.assertEqual(sorted(started), ["first", "second"])
            self.assertTrue(first_syncer.initialized)
            self.assertTrue(second_syncer.initialized)
            self.assertFalse(first_syncer.stopped)
            self.assertFalse(second_syncer.stopped)
            self.assertIsNone(first_syncer.failure_message)
            self.assertIsNone(second_syncer.failure_message)
            self.assertEqual(first_syncer.other.view_mode, DataView.TRANSACTIONS)
            self.assertEqual(second_syncer.other.view_mode, DataView.RESOURCE)

            first_syncer.stop()
            second_syncer.stop()
            self.assertEqual(sorted(stopped), ["first", "second"])
