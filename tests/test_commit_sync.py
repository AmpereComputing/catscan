# Copyright (c) 2024-2025 Ampere Computing. All rights reserved.
# SPDX-License-Identifier: BSD-3-Clause

import tempfile
import time
import unittest
from fractions import Fraction

from catscan.commit_sync import *


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
            CommitSyncState(inum=42, cycles_per_char=Fraction(1, 16), expand_rows=False, chars_rel_to_start=-47)
        )

        # Alternate sending a few sync messages each direction
        for i in range(3):
            self.first_syncer.send(
                CommitSyncState(inum=i, cycles_per_char=Fraction(2**i), expand_rows=True, chars_rel_to_start=10 - i)
            )
            self.second_syncer.send(
                CommitSyncState(inum=i * 3, cycles_per_char=Fraction(8**i), expand_rows=True, chars_rel_to_start=30 + i)
            )
            self.wait_for_messages(first_messages=i + 1, second_messages=i + 2)

        # Make sure the inums received match what was sent
        self.assertEqual([m.inum for m in self.first_incoming_messages], [0, 3, 6])
        self.assertEqual([m.inum for m in self.second_incoming_messages], [42, 0, 1, 2])

        # And the other fields, too
        self.assertEqual(self.second_incoming_messages[0].cycles_per_char, Fraction(1, 16))
        self.assertEqual(self.second_incoming_messages[0].expand_rows, False)
        self.assertEqual(self.second_incoming_messages[0].chars_rel_to_start, -47)
        self.assertEqual(self.second_incoming_messages[1].expand_rows, True)

    def test_stop(self):
        self.first_syncer.send(
            CommitSyncState(inum=42, cycles_per_char=Fraction(1, 16), expand_rows=True, chars_rel_to_start=-47)
        )

        self.first_syncer.stop()
        self.wait_for_stop()

        # This message should be dropped after logging the stopped sync.
        with self.assertLogs(level="WARNING") as logs:
            self.second_syncer.send(
                CommitSyncState(inum=49, cycles_per_char=Fraction(32, 1), expand_rows=True, chars_rel_to_start=109)
            )
        self.assertIn("Dropping to-send commit sync message", logs.output[0])

        self.assertTrue(self.first_stopped)
        self.assertTrue(self.second_stopped)

        self.wait_for_messages(first_messages=0, second_messages=1)
        self.assertEqual(self.second_incoming_messages[0].inum, 42)

        # Ensure we never received the message sent after the FIFO was stopped
        time.sleep(2)
        self.assertEqual(len(self.first_incoming_messages), 0)
