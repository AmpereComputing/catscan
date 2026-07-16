# Copyright (c) 2026 Ampere Computing. All rights reserved.
# SPDX-License-Identifier: BSD-3-Clause

from fractions import Fraction

from catscan.commit_sync import CommitSyncState
from catscan.data import DataView
from catscan.state import Selection
from catscan.widgets.event_row import RowType
from catscan.widgets.resource_view import SubsetResourceView
from tests import DummyCommitSyncer, DummyMainLoop, TransactionSyncDataTest


class TestResourceViewViewport(TransactionSyncDataTest):
    def test_resource_selection_change_emits_position_notifications(self):
        view, events = self.make_resource_view()
        size = (80, 1)
        view.render(size, focus=True)
        events.clear()

        view.update_selected_row(Selection("core.commit"))

        self.assertEqual(view.focused_row(), (RowType.EVENT, "core.commit"))
        self.assertEqual(view.top_visible_row_key(), "core.commit")
        self.assertEqual(events[-1], "core.commit")

    def test_subset_resource_selection_change_emits_position_notifications(self):
        view, events = self.make_resource_view(SubsetResourceView, max_rows=2)
        size = (80, 1)
        view.add_event("work")
        view.add_event("core.commit")
        view.render(size, focus=True)
        events.clear()

        view.update_selected_row(Selection("core.commit"))

        self.assertEqual(view.focused_row(), (RowType.EVENT, "core.commit"))
        self.assertEqual(view.top_visible_row_key(), "core.commit")
        self.assertEqual(events[-1], "core.commit")

    def test_resource_commit_sync_returns_pushout_events(self):
        view, _events = self.make_resource_view()
        syncer = DummyCommitSyncer(
            my_pushout_index={20: 4},
            other_pushout_index={20: 1},
            view_mode=DataView.RESOURCE,
        )

        pushout_events = view.start_commit_sync(syncer, "core.commit", "core.inum")

        self.assertEqual([event.name for event in pushout_events], ["Events.excess_commit_pushout"] * 3)
        self.assertEqual([event.time for event in pushout_events], [20, 30, 40])
        self.assertEqual(
            [event.id for event in pushout_events],
            list(range(view.stream_data.max_event_id + 1, view.stream_data.max_event_id + 4)),
        )
        self.assertEqual(
            [event.data for event in pushout_events],
            [
                {
                    "txid": self.txids[1],
                    "sync_index": 20,
                    "pushout": 4,
                    "other_pushout": 1,
                    "excess_pushout": 3,
                },
            ]
            * 3,
        )
        self.assertEqual(view.commit_sync_index_candidates(), [10, 11, 20, 40, 41, 50])

    def test_subset_resource_commit_sync_opts_out(self):
        view, _events = self.make_resource_view(SubsetResourceView, max_rows=2)
        view.add_event("core.commit")
        syncer = DummyCommitSyncer(
            my_pushout_index={20: 4},
            other_pushout_index={20: 1},
            view_mode=DataView.RESOURCE,
        )

        pushout_events = view.start_commit_sync(syncer, "core.commit", "core.inum")

        self.assertEqual(pushout_events, [])
        self.assertEqual(view.commit_sync_index_candidates(), [])


class TestTopResourceCommitSync(TransactionSyncDataTest):
    def test_resource_view_receives_transaction_row_sync_as_commit_time(self):
        top = self.make_top(view=DataView.RESOURCE)
        top.commit_syncer = DummyCommitSyncer(view_mode=DataView.RESOURCE, other_view_mode=DataView.TRANSACTIONS)
        sync_cycles_per_char = Fraction(1, 8)
        sync_expand_rows = not top.state.expand_rows

        top.receive_commit_sync(
            CommitSyncState(
                sync_index=40,
                cycles_per_char=sync_cycles_per_char,
                expand_rows=sync_expand_rows,
                chars_rel_to_start=17,
                movement_alignment="after",
            )
        )

        self.assertEqual(top.state.start_ps, 100)
        self.assertEqual(top.state.cycles_per_char, sync_cycles_per_char)
        self.assertEqual(top.state.expand_rows, sync_expand_rows)

    def test_resource_time_sync_uses_local_zoom_when_sender_omits_zoom(self):
        top = self.make_top(view=DataView.RESOURCE)
        top.update_state(top.state.copy_with(cycles_per_char=Fraction(2, 1), start_ps=0))
        top.commit_syncer = DummyCommitSyncer(view_mode=DataView.RESOURCE, other_view_mode=DataView.RESOURCE)

        top.receive_commit_sync(
            CommitSyncState(
                sync_index=40,
                chars_rel_to_start=3,
            )
        )

        self.assertEqual(top.state.start_ps, 160)
        self.assertEqual(top.state.cycles_per_char, Fraction(2, 1))

    def test_resource_sender_time_sync_scrolls_transaction_receiver(self):
        sender = self.make_top(view=DataView.RESOURCE)
        receiver = self.make_top()
        sender.update_state(sender.state.copy_with(start_ps=100, cycles_per_char=Fraction(1, 8), expand_rows=True))
        sender.commit_syncer = DummyCommitSyncer(
            other_commit_index=receiver.commit_sync_index,
            other_pushout_index=receiver.pushout_index,
            view_mode=DataView.RESOURCE,
            other_view_mode=DataView.TRANSACTIONS,
        )
        receiver.commit_syncer = DummyCommitSyncer(other_view_mode=DataView.RESOURCE)

        sender.send_commit_sync()

        self.assertEqual(len(sender.commit_syncer.sent), 1)
        self.assertEqual(sender.commit_syncer.sent[0].sync_index, 40)
        self.assertEqual(sender.commit_syncer.sent[0].chars_rel_to_start, 0)
        receiver.receive_commit_sync(sender.commit_syncer.sent[0])
        self.assertEqual(receiver._transaction_view.top_visible_row_key(), self.txids[3])
        self.assertEqual(receiver.state.cycles_per_char, sender.state.cycles_per_char)
        self.assertEqual(receiver.state.expand_rows, sender.state.expand_rows)

    def test_resource_sender_without_shared_anchor_sends_display_state(self):
        top = self.make_top(view=DataView.RESOURCE)
        top.update_state(top.state.copy_with(cycles_per_char=Fraction(1, 8), expand_rows=True))
        top.commit_syncer = DummyCommitSyncer(
            other_commit_index={999: 999},
            view_mode=DataView.RESOURCE,
            other_view_mode=DataView.RESOURCE,
        )

        top.send_commit_sync()

        self.assertEqual(len(top.commit_syncer.sent), 1)
        self.assertIsNone(top.commit_syncer.sent[0].sync_index)
        self.assertIsNone(top.commit_syncer.sent[0].chars_rel_to_start)
        self.assertEqual(top.commit_syncer.sent[0].cycles_per_char, Fraction(1, 8))
        self.assertTrue(top.commit_syncer.sent[0].expand_rows)

    def test_resource_start_commit_sync_without_pushouts_does_not_save_stream_data(self):
        top = self.make_top(view=DataView.RESOURCE, start_commit_sync=False)
        top.main_loop = DummyMainLoop()
        top.commit_syncer = DummyCommitSyncer(view_mode=DataView.RESOURCE)

        top.start_commit_sync()

        self.assertFalse(hasattr(top, "saved_stream_data"))

    def test_resource_start_commit_sync_with_pushouts_saves_stream_data(self):
        top = self.make_top(view=DataView.RESOURCE, start_commit_sync=False)
        top.main_loop = DummyMainLoop()
        original_stream_data = top.stream_data
        top.commit_syncer = DummyCommitSyncer(
            my_pushout_index={20: 4},
            other_pushout_index={20: 1},
            view_mode=DataView.RESOURCE,
        )

        top.start_commit_sync()

        self.assertIs(top.saved_stream_data, original_stream_data)
        self.assertIsNot(top.stream_data, original_stream_data)
