# Copyright (c) 2026 Ampere Computing. All rights reserved.
# SPDX-License-Identifier: BSD-3-Clause

from fractions import Fraction

from catscan.commit_sync import CommitSyncState
from catscan.data import DataView
from catscan.state import Selection
from tests import DummyCommitSyncer, TransactionSyncDataTest


class TestTransactionViewViewport(TransactionSyncDataTest):
    def test_top_row_changes_on_key_navigation(self):
        view, events = self.make_transaction_view()
        size = (80, 1)
        view.render(size, focus=True)
        events.clear()

        view.keypress(size, "down")
        self.assertEqual(view.top_visible_row_key(), self.txids[1])

        view.keypress(size, "G")
        self.assertEqual(view.top_visible_row_key(), self.txids[-1])

        view.keypress(size, "g-g")
        self.assertEqual(view.top_visible_row_key(), self.txids[0])
        self.assertIn(self.txids[1], events)

    def test_top_row_changes_on_half_page_navigation(self):
        view, events = self.make_transaction_view()
        size = (80, 2)
        view.render(size, focus=True)
        initial_top = view.top_visible_row_key()
        events.clear()

        view.keypress(size, "ctrl d")
        view.keypress(size, "ctrl d")
        self.assertNotEqual(view.top_visible_row_key(), initial_top)

        view.keypress(size, "ctrl u")
        view.keypress(size, "ctrl u")
        self.assertEqual(view.top_visible_row_key(), initial_top)
        self.assertTrue(events)

    def test_top_row_changes_on_mouse_scroll(self):
        view, events = self.make_transaction_view()
        size = (80, 1)
        view.render(size, focus=True)
        events.clear()

        view.mouse_event(size, "mouse press", 5, 5, 0, True)
        self.assertEqual(view.top_visible_row_key(), self.txids[1])
        self.assertEqual(events[-1], self.txids[1])

    def test_top_row_changes_on_programmatic_focus(self):
        view, events = self.make_transaction_view()
        size = (80, 1)
        view.render(size, focus=True)
        events.clear()

        view.update_selected_row(Selection(self.txids[3]))
        self.assertEqual(view.top_visible_row_key(), self.txids[3])
        self.assertEqual(events[-1], self.txids[3])

    def test_drag_pan_emits_viewport_change(self):
        view, events = self.make_transaction_view()
        size = (80, 3)
        view.render(size, focus=True)
        view.keypress(size, "page down")
        events.clear()

        view.mouse_event(size, "mouse drag", 1, 5, 2, True)
        view.mouse_event(size, "mouse drag", 1, 5, 1, True)
        self.assertTrue(events)

    def test_scroll_row_to_top_aligns_requested_row(self):
        view, _events = self.make_transaction_view()
        size = (80, 3)
        view.render(size, focus=True)

        self.assertTrue(view.scroll_row_to_top(self.txids[3]))
        self.assertEqual(view.top_visible_row_key(), self.txids[3])

    def test_scroll_row_to_bottom_aligns_requested_row(self):
        view, _events = self.make_transaction_view()
        size = (80, 3)
        view.render(size, focus=True)

        self.assertTrue(view.scroll_row_to_edge(self.txids[3], "bottom"))
        self.assertEqual(view.focused_row()[1], self.txids[3])
        self.assertEqual(view.visible_row_keys()[-1], self.txids[3])

    def test_focus_row_change_receives_movement_alignment(self):
        alignments = []
        view, _events = self.make_transaction_view(
            focus_callback=lambda _view, _focused_row_key, movement_alignment: alignments.append(movement_alignment)
        )
        size = (80, 1)
        view.render(size, focus=True)
        alignments.clear()

        view.keypress(size, "down")
        view.keypress(size, "up")

        self.assertEqual(alignments, ["after", "before"])


class TestTopTransactionCommitSync(TransactionSyncDataTest):
    def test_focused_row_with_one_shared_commit_anchors_correctly(self):
        top = self.make_top()
        top._transaction_view.scroll_row_to_top(self.txids[1])
        top.commit_syncer = DummyCommitSyncer(other_commit_index={20: 50})

        top.send_commit_sync()

        self.assertEqual(len(top.commit_syncer.sent), 1)
        self.assertEqual(top.commit_syncer.sent[0].sync_index, 20)

    def test_selecting_visible_non_top_row_sends_focused_row_anchor(self):
        top = self.make_top(size=(120, 8))
        top.commit_syncer = DummyCommitSyncer(other_commit_index={40: 100, 41: 110})

        top.make_selection(Selection(self.txids[3], view=top._transaction_view.name))

        self.assertEqual(len(top.commit_syncer.sent), 1)
        self.assertEqual(top.commit_syncer.sent[0].sync_index, 40)

    def test_send_commit_sync_without_scroll_omits_alignment(self):
        top = self.make_top()
        top.commit_syncer = DummyCommitSyncer(other_commit_index={10: 10, 11: 20})

        top.send_commit_sync()

        self.assertEqual(len(top.commit_syncer.sent), 1)
        self.assertIsNone(top.commit_syncer.sent[0].movement_alignment)

    def test_transaction_sender_uses_primary_view(self):
        top = self.make_top()
        top.commit_syncer = DummyCommitSyncer(
            other_commit_index=top.commit_sync_index,
            view_mode=DataView.RESOURCE,
        )

        top.send_commit_sync()

        self.assertEqual(len(top.commit_syncer.sent), 1)
        self.assertEqual(top.commit_syncer.sent[0].sync_index, 10)
        self.assertEqual(top.commit_syncer.sent[0].chars_rel_to_start, 0)

    def test_earliest_shared_commit_in_row_is_used(self):
        top = self.make_top()
        top._transaction_view.scroll_row_to_top(self.txids[0])
        top.commit_syncer = DummyCommitSyncer(other_commit_index={10: 10, 11: 20})

        top.send_commit_sync()

        self.assertEqual(len(top.commit_syncer.sent), 1)
        self.assertEqual(top.commit_syncer.sent[0].sync_index, 10)

    def test_row_with_no_shared_commit_sends_display_only_state(self):
        top = self.make_top()
        top._transaction_view.scroll_row_to_top(self.txids[2])
        top.commit_syncer = DummyCommitSyncer(other_commit_index={999: 999}, other_view_mode=DataView.TRANSACTIONS)

        top.send_commit_sync()

        self.assertEqual(len(top.commit_syncer.sent), 1)
        self.assertIsNone(top.commit_syncer.sent[0].sync_index)
        self.assertIsNone(top.commit_syncer.sent[0].chars_rel_to_start)

    def test_unanchored_transaction_row_duplicate_display_state_is_suppressed(self):
        top = self.make_top()
        top.commit_syncer = DummyCommitSyncer(other_commit_index={999: 999}, other_view_mode=DataView.TRANSACTIONS)

        top._transaction_view.scroll_row_to_top_for_commit_sync(self.txids[0])
        top.send_commit_sync()
        top._transaction_view.scroll_row_to_top_for_commit_sync(self.txids[1])
        top.send_commit_sync()

        self.assertEqual(len(top.commit_syncer.sent), 1)
        self.assertIsNone(top.commit_syncer.sent[0].sync_index)

    def test_zoom_change_on_same_transaction_row_sends_again(self):
        top = self.make_top()
        top.commit_syncer = DummyCommitSyncer(other_commit_index={10: 10, 11: 20})

        top.send_commit_sync()
        top.update_state(top.state.copy_with(cycles_per_char=Fraction(1, 2)))

        self.assertEqual(len(top.commit_syncer.sent), 2)
        self.assertEqual([state.sync_index for state in top.commit_syncer.sent], [10, None])
        self.assertIsNone(top.commit_syncer.sent[1].chars_rel_to_start)
        self.assertEqual(top.commit_syncer.sent[1].cycles_per_char, Fraction(1, 2))

    def test_expand_change_on_same_transaction_row_sends_again(self):
        top = self.make_top()
        top.commit_syncer = DummyCommitSyncer(other_commit_index={10: 10, 11: 20})

        top.send_commit_sync()
        top.update_state(top.state.copy_with(expand_rows=True))

        self.assertEqual(len(top.commit_syncer.sent), 2)
        self.assertEqual([state.sync_index for state in top.commit_syncer.sent], [10, None])
        self.assertIsNone(top.commit_syncer.sent[1].chars_rel_to_start)
        self.assertTrue(top.commit_syncer.sent[1].expand_rows)

    def test_non_commit_event_with_index_is_not_transaction_anchor(self):
        top = self.make_top()
        top._transaction_view.scroll_row_to_top_for_commit_sync(self.txids[2])

        self.assertIsNone(top._transaction_view.commit_sync_row(999))
        self.assertEqual(top._transaction_view.commit_sync_index_candidates(), [])

    def test_transaction_anchors_are_lazy(self):
        top = self.make_top(start_commit_sync=False)

        self.assertIsNone(top._transaction_view.commit_sync_row(10))
        self.assertEqual(top._transaction_view.commit_sync_index_candidates(), [])

        anchors = top._transaction_view.start_commit_sync(
            DummyCommitSyncer(other_commit_index=top.commit_sync_index),
            top.commit_sync_event,
            top.commit_sync_data_name,
        )

        self.assertEqual(anchors, [])
        self.assertEqual(top._transaction_view.commit_sync_row(10), self.txids[0])
        self.assertEqual(top._transaction_view.commit_sync_index_candidates(), [10, 11])

    def test_transaction_anchors_clear_when_sync_stops(self):
        top = self.make_top()

        top._transaction_view.stop_commit_sync()

        self.assertIsNone(top._transaction_view.commit_sync_row(10))
        self.assertEqual(top._transaction_view.commit_sync_index_candidates(), [])

    def test_scrolling_down_sends_after_alignment(self):
        top = self.make_top(size=(120, 2))
        top.commit_syncer = DummyCommitSyncer(other_commit_index=top.commit_sync_index)

        top._transaction_view.keypress((120, 2), "down")

        self.assertEqual(len(top.commit_syncer.sent), 1)
        self.assertEqual(top.commit_syncer.sent[0].sync_index, 20)
        self.assertEqual(top.commit_syncer.sent[0].movement_alignment, "after")

    def test_scrolling_up_sends_before_alignment(self):
        top = self.make_top(size=(120, 2))
        top._transaction_view.scroll_row_to_top(self.txids[2])
        top.commit_syncer = DummyCommitSyncer(other_commit_index=top.commit_sync_index)
        top.commit_syncer.sent.clear()

        top._transaction_view.keypress((120, 2), "up")

        self.assertEqual(len(top.commit_syncer.sent), 1)
        self.assertEqual(top.commit_syncer.sent[0].sync_index, 20)
        self.assertEqual(top.commit_syncer.sent[0].movement_alignment, "before")

    def test_inbound_transaction_row_sync_scrolls_row_to_top(self):
        top = self.make_top()
        top.commit_syncer = DummyCommitSyncer(other_view_mode=DataView.TRANSACTIONS)

        top.receive_commit_sync(
            CommitSyncState(
                sync_index=40,
                cycles_per_char=Fraction(1, 1),
                expand_rows=False,
                chars_rel_to_start=0,
            )
        )

        self.assertEqual(top._transaction_view.top_visible_row_key(), self.txids[3])

    def test_inbound_transaction_row_sync_scrolls_row_to_bottom(self):
        top = self.make_top(size=(120, 3))
        top.commit_syncer = DummyCommitSyncer(other_view_mode=DataView.TRANSACTIONS)

        top.receive_commit_sync(
            CommitSyncState(
                sync_index=40,
                cycles_per_char=Fraction(1, 1),
                expand_rows=False,
                chars_rel_to_start=0,
                movement_alignment="after",
            )
        )

        self.assertEqual(top._transaction_view.focused_row()[1], self.txids[3])
        self.assertEqual(top._transaction_view.visible_row_keys()[-1], self.txids[3])

    def test_inbound_transaction_row_sync_applies_display_state_without_panning(self):
        top = self.make_top()
        top.commit_syncer = DummyCommitSyncer(other_view_mode=DataView.TRANSACTIONS)
        requested_state = top.state.copy_with(start_ps=1230, cycles_per_char=Fraction(4, 1), expand_rows=True)
        top.update_state(requested_state)
        initial_state = top.state
        sync_cycles_per_char = Fraction(1, 8)
        sync_expand_rows = False

        top.receive_commit_sync(
            CommitSyncState(
                sync_index=50,
                cycles_per_char=sync_cycles_per_char,
                expand_rows=sync_expand_rows,
                chars_rel_to_start=17,
            )
        )

        self.assertEqual(top.state.start_ps, initial_state.start_ps)
        self.assertEqual(top.state.cycles_per_char, sync_cycles_per_char)
        self.assertEqual(top.state.expand_rows, sync_expand_rows)

    def test_transaction_view_receives_resource_time_sync_as_row_scroll(self):
        top = self.make_top()
        top.commit_syncer = DummyCommitSyncer(other_view_mode=DataView.RESOURCE)
        sync_cycles_per_char = Fraction(1, 8)
        sync_expand_rows = True

        top.receive_commit_sync(
            CommitSyncState(
                sync_index=40,
                cycles_per_char=sync_cycles_per_char,
                expand_rows=sync_expand_rows,
                chars_rel_to_start=17,
            )
        )

        self.assertEqual(top._transaction_view.top_visible_row_key(), self.txids[3])
        self.assertEqual(top.state.cycles_per_char, sync_cycles_per_char)
        self.assertEqual(top.state.expand_rows, sync_expand_rows)

    def test_resource_time_sync_applies_display_state_without_panning(self):
        top = self.make_top()
        top.commit_syncer = DummyCommitSyncer(other_view_mode=DataView.RESOURCE)
        requested_state = top.state.copy_with(start_ps=1230, cycles_per_char=Fraction(4, 1), expand_rows=True)
        top.update_state(requested_state)
        initial_state = top.state
        sync_cycles_per_char = Fraction(1, 8)
        sync_expand_rows = False

        top.receive_commit_sync(
            CommitSyncState(
                sync_index=50,
                cycles_per_char=sync_cycles_per_char,
                expand_rows=sync_expand_rows,
                chars_rel_to_start=17,
            )
        )

        self.assertEqual(top.state.start_ps, initial_state.start_ps)
        self.assertEqual(top.state.cycles_per_char, sync_cycles_per_char)
        self.assertEqual(top.state.expand_rows, sync_expand_rows)

    def test_missing_resource_time_sync_index_does_not_scroll_transaction_view(self):
        top = self.make_top()
        top.commit_syncer = DummyCommitSyncer(other_view_mode=DataView.RESOURCE)
        initial_focus = top._transaction_view.focused_row()[1]
        initial_top = top._transaction_view.top_visible_row_key()

        top.receive_commit_sync(
            CommitSyncState(
                sync_index=12345,
                cycles_per_char=Fraction(1, 8),
                expand_rows=True,
                chars_rel_to_start=17,
            )
        )

        self.assertEqual(top._transaction_view.focused_row()[1], initial_focus)
        self.assertEqual(top._transaction_view.top_visible_row_key(), initial_top)

    def test_display_only_sync_does_not_move_transaction_view(self):
        top = self.make_top(size=(120, 3))
        top._transaction_view.scroll_row_to_top(self.txids[3])
        top.update_state(top.state.copy_with(start_ps=1230, cycles_per_char=Fraction(4, 1), expand_rows=False))
        top.commit_syncer = DummyCommitSyncer(other_view_mode=DataView.TRANSACTIONS)
        initial_focus = top._transaction_view.focused_row()[1]
        initial_top = top._transaction_view.top_visible_row_key()
        initial_start_ps = top.state.start_ps

        top.receive_commit_sync(
            CommitSyncState(
                cycles_per_char=Fraction(1, 8),
            )
        )

        self.assertEqual(top._transaction_view.focused_row()[1], initial_focus)
        self.assertEqual(top._transaction_view.top_visible_row_key(), initial_top)
        self.assertEqual(top.state.start_ps, initial_start_ps)
        self.assertEqual(top.state.cycles_per_char, Fraction(1, 8))
        self.assertFalse(top.state.expand_rows)

    def test_expand_only_display_sync_preserves_transaction_view_and_zoom(self):
        top = self.make_top(size=(120, 3))
        top._transaction_view.scroll_row_to_top(self.txids[3])
        top.update_state(top.state.copy_with(start_ps=1230, cycles_per_char=Fraction(4, 1), expand_rows=False))
        top.commit_syncer = DummyCommitSyncer(other_view_mode=DataView.TRANSACTIONS)
        initial_focus = top._transaction_view.focused_row()[1]
        initial_top = top._transaction_view.top_visible_row_key()
        initial_start_ps = top.state.start_ps

        top.receive_commit_sync(CommitSyncState(expand_rows=True))

        self.assertEqual(top._transaction_view.focused_row()[1], initial_focus)
        self.assertEqual(top._transaction_view.top_visible_row_key(), initial_top)
        self.assertEqual(top.state.start_ps, initial_start_ps)
        self.assertEqual(top.state.cycles_per_char, Fraction(4, 1))
        self.assertTrue(top.state.expand_rows)

    def test_remote_resource_time_scroll_does_not_echo_transaction_sync(self):
        top = self.make_top(size=(120, 2))
        top.commit_syncer = DummyCommitSyncer(other_commit_index={40: 100}, other_view_mode=DataView.RESOURCE)
        sync_cycles_per_char = Fraction(1, 8)
        sync_expand_rows = True

        top.receive_commit_sync(
            CommitSyncState(
                sync_index=40,
                cycles_per_char=sync_cycles_per_char,
                expand_rows=sync_expand_rows,
                chars_rel_to_start=17,
            )
        )

        self.assertEqual(top.commit_syncer.sent, [])
        self.assertFalse(top._transaction_view.suppressing_commit_sync)
        self.assertEqual(top.state.cycles_per_char, sync_cycles_per_char)
        self.assertEqual(top.state.expand_rows, sync_expand_rows)

    def test_remote_transaction_row_scroll_does_not_echo(self):
        top = self.make_top(size=(120, 2))
        top.commit_syncer = DummyCommitSyncer(other_commit_index={40: 100}, other_view_mode=DataView.TRANSACTIONS)
        sync_cycles_per_char = Fraction(1, 8)
        sync_expand_rows = True

        top.receive_commit_sync(
            CommitSyncState(
                sync_index=40,
                cycles_per_char=sync_cycles_per_char,
                expand_rows=sync_expand_rows,
                chars_rel_to_start=0,
            )
        )

        self.assertEqual(top.commit_syncer.sent, [])
        self.assertFalse(top._transaction_view.suppressing_commit_sync)
        self.assertEqual(top.state.cycles_per_char, sync_cycles_per_char)
        self.assertEqual(top.state.expand_rows, sync_expand_rows)

    def test_different_window_sizes_focus_same_selected_row(self):
        sender = self.make_top(size=(120, 5))
        receiver = self.make_top(size=(120, 2))
        sender.update_state(sender.state.copy_with(cycles_per_char=Fraction(1, 8), expand_rows=True))

        sender.commit_syncer = DummyCommitSyncer(
            other_commit_index=receiver.commit_sync_index,
            other_view_mode=DataView.TRANSACTIONS,
        )
        receiver.commit_syncer = DummyCommitSyncer(other_view_mode=DataView.TRANSACTIONS)
        sender.make_selection(Selection(self.txids[3], view=sender._transaction_view.name))

        self.assertEqual(len(sender.commit_syncer.sent), 1)
        receiver.receive_commit_sync(sender.commit_syncer.sent[0])

        self.assertEqual(receiver._transaction_view.focused_row()[1], self.txids[3])
        self.assertEqual(receiver._transaction_view.top_visible_row_key(), self.txids[3])
        self.assertEqual(receiver.state.cycles_per_char, sender.state.cycles_per_char)
        self.assertEqual(receiver.state.expand_rows, sender.state.expand_rows)

    def test_inbound_visible_transaction_row_does_not_refocus(self):
        top = self.make_top(size=(120, 8))
        top.commit_syncer = DummyCommitSyncer(other_view_mode=DataView.TRANSACTIONS)
        initial_focus = top._transaction_view.focused_row()[1]
        initial_top = top._transaction_view.top_visible_row_key()

        top.receive_commit_sync(
            CommitSyncState(
                sync_index=40,
                cycles_per_char=Fraction(1, 1),
                expand_rows=False,
                chars_rel_to_start=0,
            )
        )

        self.assertEqual(top._transaction_view.focused_row()[1], initial_focus)
        self.assertEqual(top._transaction_view.top_visible_row_key(), initial_top)
