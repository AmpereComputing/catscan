# Copyright (c) 2026 Ampere Computing. All rights reserved.
# SPDX-License-Identifier: BSD-3-Clause

import unittest
from fractions import Fraction
from types import SimpleNamespace

from perf_streams.event_stream import EventStreamWriter
from test_data import CatscanDataTest

from catscan.commit_sync import CommitSyncState
from catscan.data import DataView, get_event_data
from catscan.events import trace_events
from catscan.events.mapping import Mapper
from catscan.state import CatscanState, HashableFrozenDict, Selection
from catscan.widgets.event_row import RowType
from catscan.widgets.resource_view import ResourceView, SubsetResourceView
from catscan.widgets.top import Top
from catscan.widgets.transaction_view import TransactionView


class Args:
    def __init__(self, **kwargs):
        self.view = DataView.TRANSACTIONS
        self.cache = None
        self.instruction_arch = "arm64"
        self.period = 10
        self.sort_keys = True
        self.instruction_commit_event = "core.commit"
        self.instruction_commit_index = "core.inum"
        self.convert_enumerations = True
        self.debug = True
        self.hex = []

        self.__dict__.update(kwargs)

    def __getattr__(self, _name):
        return []


class DummyCommitSyncer:
    def __init__(self, *, other_commit_index=None, other_pushout_index=None, other_view_mode=DataView.RESOURCE):
        self.syncing = True
        self.other = SimpleNamespace(
            commit_index=other_commit_index or {},
            pushout_index=other_pushout_index or {},
            column_header_width=0,
            view_mode=other_view_mode,
            view_mode_from_handshake=True,
        )
        self.sent = []
        self.failure_message = None

    def send(self, sync_state):
        self.sent.append(sync_state)


class TransactionSyncDataTest(CatscanDataTest):
    @classmethod
    def event_stream_setup(cls):
        writer = EventStreamWriter(cls.test_filename)
        work = writer.define_event("work", "transaction work")
        commit = writer.define_event("core.commit", "committed instruction")
        inum = writer.define_data("core.inum", "committed instruction number")
        writer.start_simulation()

        cls.txids = []

        tx = writer.begin_transaction(time=0)
        cls.txids.append(tx.txid)
        writer.post_event(work, time=0, transaction=tx)
        writer.post_event(commit, time=10, transaction=tx, values={inum: 10})
        writer.post_event(commit, time=20, transaction=tx, values={inum: 11})
        writer.end_transaction(tx, time=30)

        tx = writer.begin_transaction(time=40)
        cls.txids.append(tx.txid)
        writer.post_event(work, time=40, transaction=tx)
        writer.post_event(commit, time=50, transaction=tx, values={inum: 20})
        writer.end_transaction(tx, time=60)

        tx = writer.begin_transaction(time=70)
        cls.txids.append(tx.txid)
        writer.post_event(work, time=70, transaction=tx, values={inum: 999})
        writer.end_transaction(tx, time=80)

        tx = writer.begin_transaction(time=90)
        cls.txids.append(tx.txid)
        writer.post_event(work, time=90, transaction=tx)
        writer.post_event(commit, time=100, transaction=tx, values={inum: 40})
        writer.post_event(commit, time=110, transaction=tx, values={inum: 41})
        writer.end_transaction(tx, time=120)

        tx = writer.begin_transaction(time=130)
        cls.txids.append(tx.txid)
        writer.post_event(work, time=130, transaction=tx)
        writer.post_event(commit, time=140, transaction=tx, values={inum: 50})
        writer.end_transaction(tx, time=150)

        writer.close()

        cls.set_event_stream_params(
            events=[
                trace_events.trace_spec("work"),
                trace_events.trace_spec("core.commit"),
                trace_events.trace_spec("start_transaction"),
                trace_events.trace_spec("end_transaction"),
            ]
        )

    def load_event_data(self):
        return get_event_data(
            self.test_filename,
            "transactions",
            mapper=Mapper([], [], []),
            event_filters=trace_events.EventFilters(),
            events=[
                trace_events.trace_spec("work"),
                trace_events.trace_spec("core.commit"),
                trace_events.trace_spec("start_transaction"),
                trace_events.trace_spec("end_transaction"),
            ],
            post_to_tx=[],
            pull_from_tx=[],
            occupancy=[],
            pct_loaded_callback=lambda _pct: None,
            transaction_name="abbrev",
            transaction_start=trace_events.trace_spec("work"),
            transaction_end=None,
        )

    def load_resource_event_data(self):
        return get_event_data(
            self.test_filename,
            "resource",
            mapper=Mapper([], [], []),
            event_filters=trace_events.EventFilters(),
            events=[
                trace_events.trace_spec("work"),
                trace_events.trace_spec("core.commit"),
            ],
            post_to_tx=[],
            pull_from_tx=[],
            occupancy=[],
            pct_loaded_callback=lambda _pct: None,
        )


class TestEventViewViewport(TransactionSyncDataTest):
    def make_state(self):
        return CatscanState(
            has_focus=True,
            loading=False,
            ps_per_cycle=10,
            column_header_width=8,
            cycles_per_char=Fraction(1, 1),
            start_ps=0,
            start_row=1,
            expand_rows=False,
            selection=Selection(),
            sort_event_keys=True,
            highlighted_transactions=HashableFrozenDict(),
            marked_events=HashableFrozenDict(),
            searcher=None,
            messages=[],
            show_help=False,
        )

    def make_view(self, callback=None, focus_callback=None):
        events = []
        view = TransactionView(
            "transaction",
            self.make_state(),
            self.esd,
            on_zoom_in=lambda *_args, **_kwargs: False,
            on_zoom_out=lambda *_args, **_kwargs: False,
            on_scroll_left=lambda *_args, **_kwargs: False,
            on_scroll_right=lambda *_args, **_kwargs: False,
            on_toggle_expanded=lambda *_args, **_kwargs: False,
            on_make_selection=lambda *_args, **_kwargs: False,
            on_extend_selection=lambda *_args, **_kwargs: False,
            on_translate_event=lambda *_args, **_kwargs: False,
            on_viewport_change=callback or (lambda view, _align: events.append(view.top_visible_row_key())),
            on_focus_row_change=focus_callback,
        )
        return view, events

    def make_resource_data(self):
        return self.load_resource_event_data()

    def make_resource_view(self, view_type=ResourceView, **kwargs):
        events = []
        view = view_type(
            "resource",
            self.make_state(),
            self.make_resource_data(),
            on_zoom_in=lambda *_args, **_kwargs: False,
            on_zoom_out=lambda *_args, **_kwargs: False,
            on_scroll_left=lambda *_args, **_kwargs: False,
            on_scroll_right=lambda *_args, **_kwargs: False,
            on_toggle_expanded=lambda *_args, **_kwargs: False,
            on_make_selection=lambda *_args, **_kwargs: False,
            on_extend_selection=lambda *_args, **_kwargs: False,
            on_translate_event=lambda *_args, **_kwargs: False,
            on_viewport_change=lambda view, _align: events.append(view.top_visible_row_key()),
            **kwargs,
        )
        return view, events

    def test_top_row_changes_on_key_navigation(self):
        view, events = self.make_view()
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
        view, events = self.make_view()
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
        view, events = self.make_view()
        size = (80, 1)
        view.render(size, focus=True)
        events.clear()

        view.mouse_event(size, "mouse press", 5, 5, 0, True)
        self.assertEqual(view.top_visible_row_key(), self.txids[1])
        self.assertEqual(events[-1], self.txids[1])

    def test_top_row_changes_on_programmatic_focus(self):
        view, events = self.make_view()
        size = (80, 1)
        view.render(size, focus=True)
        events.clear()

        view.update_selected_row(Selection(self.txids[3]))
        self.assertEqual(view.top_visible_row_key(), self.txids[3])
        self.assertEqual(events[-1], self.txids[3])

    def test_drag_pan_emits_viewport_change(self):
        view, events = self.make_view()
        size = (80, 3)
        view.render(size, focus=True)
        view.keypress(size, "page down")
        events.clear()

        view.mouse_event(size, "mouse drag", 1, 5, 2, True)
        view.mouse_event(size, "mouse drag", 1, 5, 1, True)
        self.assertTrue(events)

    def test_scroll_row_to_top_aligns_requested_row(self):
        view, _events = self.make_view()
        size = (80, 3)
        view.render(size, focus=True)

        self.assertTrue(view.scroll_row_to_top(self.txids[3]))
        self.assertEqual(view.top_visible_row_key(), self.txids[3])

    def test_scroll_row_to_bottom_aligns_requested_row(self):
        view, _events = self.make_view()
        size = (80, 3)
        view.render(size, focus=True)

        self.assertTrue(view.scroll_row_to_edge(self.txids[3], "bottom"))
        self.assertEqual(view.focused_row()[1], self.txids[3])
        self.assertEqual(view.visible_row_keys()[-1], self.txids[3])

    def test_vertical_direction_tracks_navigation(self):
        view, _events = self.make_view()
        size = (80, 1)
        view.render(size, focus=True)

        view.keypress(size, "down")
        self.assertEqual(view.last_vertical_direction(), "down")

        view.keypress(size, "up")
        self.assertEqual(view.last_vertical_direction(), "up")

    def test_focus_row_change_receives_movement_alignment(self):
        alignments = []
        view, _events = self.make_view(
            focus_callback=lambda _view, _focused_row_key, movement_alignment: alignments.append(movement_alignment)
        )
        size = (80, 1)
        view.render(size, focus=True)
        alignments.clear()

        view.keypress(size, "down")
        view.keypress(size, "up")

        self.assertEqual(alignments, ["after", "before"])

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


class TestTopTransactionCommitSync(TransactionSyncDataTest):
    def make_top(self, size=(120, 4), view=DataView.TRANSACTIONS):
        top = Top(Args(view=view))
        top.cached_maxcol = 120
        stream_data = self.load_resource_event_data() if view == DataView.RESOURCE else self.esd
        top.update_stream_data(stream_data)
        top.render(size, focus=True)
        return top

    def test_focused_row_with_one_shared_commit_anchors_correctly(self):
        top = self.make_top()
        top._transaction_view.scroll_row_to_top(self.txids[1])
        top.commit_syncer = DummyCommitSyncer(other_commit_index={20: 50})

        top.send_commit_sync()

        self.assertEqual(len(top.commit_syncer.sent), 1)
        self.assertEqual(top.commit_syncer.sent[0].sync_index, 20)
        self.assertIsNone(top.commit_syncer.sent[0].mode)

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

        self.assertNotIn(999, top.commit_sync_index_to_transaction_row)
        self.assertNotIn(self.txids[2], top.transaction_row_commit_candidates)

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

    def test_resource_view_receives_transaction_row_sync_as_commit_time(self):
        top = self.make_top(view=DataView.RESOURCE)
        top.commit_syncer = DummyCommitSyncer(other_view_mode=DataView.TRANSACTIONS)
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
                sync_index=None,
                cycles_per_char=Fraction(1, 8),
                expand_rows=True,
                chars_rel_to_start=None,
            )
        )

        self.assertEqual(top._transaction_view.focused_row()[1], initial_focus)
        self.assertEqual(top._transaction_view.top_visible_row_key(), initial_top)
        self.assertEqual(top.state.start_ps, initial_start_ps)
        self.assertEqual(top.state.cycles_per_char, Fraction(1, 8))
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
        self.assertFalse(top._suppress_transaction_commit_sync)
        self.assertEqual(top.state.cycles_per_char, sync_cycles_per_char)
        self.assertEqual(top.state.expand_rows, sync_expand_rows)

    def test_resource_sender_time_sync_scrolls_transaction_receiver(self):
        sender = self.make_top(view=DataView.RESOURCE)
        receiver = self.make_top()
        sender.update_state(sender.state.copy_with(start_ps=100, cycles_per_char=Fraction(1, 8), expand_rows=True))
        sender.commit_syncer = DummyCommitSyncer(
            other_commit_index=receiver.commit_sync_index,
            other_pushout_index=receiver.pushout_index,
            other_view_mode=DataView.TRANSACTIONS,
        )
        receiver.commit_syncer = DummyCommitSyncer(other_view_mode=DataView.RESOURCE)

        sender.send_commit_sync()

        self.assertEqual(len(sender.commit_syncer.sent), 1)
        self.assertIsNone(sender.commit_syncer.sent[0].mode)
        receiver.receive_commit_sync(sender.commit_syncer.sent[0])
        self.assertEqual(receiver._transaction_view.top_visible_row_key(), self.txids[3])
        self.assertEqual(receiver.state.cycles_per_char, sender.state.cycles_per_char)
        self.assertEqual(receiver.state.expand_rows, sender.state.expand_rows)

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
        self.assertFalse(top._suppress_transaction_commit_sync)
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
