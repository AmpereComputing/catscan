# Copyright (c) 2024-2025 Ampere Computing. All rights reserved.
# SPDX-License-Identifier: BSD-3-Clause

import json
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

    def test_state_json_round_trip_supports_modes(self):
        encoder = CommitSyncStateJSONEncoder()
        decoder = CommitSyncStateJSONDecoder()

        for mode in ("time", "transaction_row"):
            state = CommitSyncState(
                sync_index=42,
                cycles_per_char=Fraction(3, 2),
                expand_rows=True,
                chars_rel_to_start=-7,
                mode=mode,
                movement_alignment="after" if mode == "transaction_row" else None,
            )
            encoded = encoder.encode(state)
            self.assertEqual(decoder.decode(encoder.encode(state)), state)
            encoded_state = json.loads(encoded)
            if mode == "transaction_row":
                self.assertEqual(encoded_state["movement_alignment"], "after")
                self.assertEqual(encoded_state["transaction_row_align"], "bottom")
            else:
                self.assertNotIn("transaction_row_align", encoded_state)

    def test_state_json_decode_defaults_mode_to_time(self):
        decoder = CommitSyncStateJSONDecoder()

        state = decoder.decode(
            '{"sync_index": 9, "cycles_per_char": {"numerator": 5, "denominator": 4}, '
            '"expand_rows": false, "chars_rel_to_start": 11}'
        )

        self.assertEqual(state.mode, "time")
        self.assertIsNone(state.movement_alignment)
        self.assertEqual(state.sync_index, 9)
        self.assertEqual(state.cycles_per_char, Fraction(5, 4))

    def test_state_json_decode_defaults_movement_alignment_to_none(self):
        decoder = CommitSyncStateJSONDecoder()

        state = decoder.decode(
            '{"sync_index": 9, "cycles_per_char": {"numerator": 5, "denominator": 4}, '
            '"expand_rows": false, "chars_rel_to_start": 11, "mode": "transaction_row"}'
        )

        self.assertEqual(state.mode, "transaction_row")
        self.assertIsNone(state.movement_alignment)

    def test_state_json_decode_legacy_transaction_row_align(self):
        decoder = CommitSyncStateJSONDecoder()

        state = decoder.decode(
            '{"sync_index": 9, "cycles_per_char": {"numerator": 5, "denominator": 4}, '
            '"expand_rows": false, "chars_rel_to_start": 11, "mode": "transaction_row", '
            '"transaction_row_align": "bottom"}'
        )

        self.assertEqual(state.mode, "transaction_row")
        self.assertEqual(state.movement_alignment, "after")

    def test_transaction_row_receive_bypasses_pushout_scaling(self):
        received = []
        with tempfile.NamedTemporaryFile() as tmpfile:
            syncer = CommitSyncer(tmpfile.name, lambda: None, lambda: None, received.append)

        syncer.initialized = True
        syncer.stopped = False
        syncer.outgoing = object()
        syncer.my.pushout_index = {10: 8, 11: 16}
        syncer.other.pushout_index = {10: 2, 11: 4}

        sync_state = CommitSyncState(
            sync_index=10,
            cycles_per_char=Fraction(1, 1),
            expand_rows=False,
            chars_rel_to_start=-9,
            mode="transaction_row",
        )
        syncer.receive(sync_state)

        self.assertEqual(received, [sync_state])
        syncer.outgoing = None
        syncer.stop()


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
