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
    def __init__(self, *, other_commit_index=None, other_pushout_index=None):
        self.syncing = True
        self.other = SimpleNamespace(
            commit_index=other_commit_index or {},
            pushout_index=other_pushout_index or {},
            column_header_width=0,
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
        writer.post_event(work, time=70, transaction=tx)
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

    def make_view(self, callback=None):
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
            on_viewport_change=callback or (lambda _view, row_key: events.append(row_key)),
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


class TestTopTransactionCommitSync(TransactionSyncDataTest):
    def make_top(self, size=(120, 4)):
        top = Top(Args())
        top.cached_maxcol = 120
        top.update_stream_data(self.esd)
        top.render(size, focus=True)
        return top

    def test_top_visible_row_with_one_shared_commit_anchors_correctly(self):
        top = self.make_top()
        top._transaction_view.scroll_row_to_top(self.txids[1])
        top.commit_syncer = DummyCommitSyncer(other_commit_index={20: 50})

        top.send_commit_sync()

        self.assertEqual(len(top.commit_syncer.sent), 1)
        self.assertEqual(top.commit_syncer.sent[0].sync_index, 20)
        self.assertEqual(top.commit_syncer.sent[0].mode, "transaction_row")

    def test_selecting_visible_non_top_row_sends_focused_row_anchor(self):
        top = self.make_top(size=(120, 5))
        top.commit_syncer = DummyCommitSyncer(other_commit_index={40: 100, 41: 110})

        top.make_selection(Selection(self.txids[3], view=top._transaction_view.name))

        self.assertEqual(len(top.commit_syncer.sent), 1)
        self.assertEqual(top.commit_syncer.sent[0].sync_index, 40)

    def test_earliest_shared_commit_in_row_is_used(self):
        top = self.make_top()
        top._transaction_view.scroll_row_to_top(self.txids[0])
        top.commit_syncer = DummyCommitSyncer(other_commit_index={10: 10, 11: 20})

        top.send_commit_sync()

        self.assertEqual(len(top.commit_syncer.sent), 1)
        self.assertEqual(top.commit_syncer.sent[0].sync_index, 10)

    def test_row_with_no_shared_commit_sends_nothing(self):
        top = self.make_top()
        top._transaction_view.scroll_row_to_top(self.txids[2])
        top.commit_syncer = DummyCommitSyncer(other_commit_index={999: 999})

        top.send_commit_sync()

        self.assertEqual(top.commit_syncer.sent, [])

    def test_inbound_transaction_row_sync_scrolls_row_to_top(self):
        top = self.make_top()

        top.receive_commit_sync(
            CommitSyncState(
                sync_index=40,
                cycles_per_char=Fraction(1, 1),
                expand_rows=False,
                chars_rel_to_start=0,
                mode="transaction_row",
            )
        )

        self.assertEqual(top._transaction_view.top_visible_row_key(), self.txids[3])

    def test_inbound_transaction_row_sync_leaves_horizontal_state_unchanged(self):
        top = self.make_top()
        requested_state = top.state.copy_with(start_ps=1230, cycles_per_char=Fraction(4, 1), expand_rows=True)
        top.update_state(requested_state)
        initial_state = top.state

        top.receive_commit_sync(
            CommitSyncState(
                sync_index=50,
                cycles_per_char=Fraction(1, 8),
                expand_rows=False,
                chars_rel_to_start=17,
                mode="transaction_row",
            )
        )

        self.assertEqual(top.state.start_ps, initial_state.start_ps)
        self.assertEqual(top.state.cycles_per_char, initial_state.cycles_per_char)
        self.assertEqual(top.state.expand_rows, initial_state.expand_rows)

    def test_remote_transaction_row_scroll_does_not_echo(self):
        top = self.make_top()
        top.commit_syncer = DummyCommitSyncer(other_commit_index={40: 100})

        top.receive_commit_sync(
            CommitSyncState(
                sync_index=40,
                cycles_per_char=Fraction(1, 1),
                expand_rows=False,
                chars_rel_to_start=0,
                mode="transaction_row",
            )
        )

        self.assertEqual(top.commit_syncer.sent, [])
        self.assertFalse(top._suppress_transaction_commit_sync)

    def test_different_window_sizes_focus_same_selected_row(self):
        sender = self.make_top(size=(120, 5))
        receiver = self.make_top(size=(120, 2))

        sender.commit_syncer = DummyCommitSyncer(other_commit_index=receiver.commit_sync_index)
        sender.make_selection(Selection(self.txids[3], view=sender._transaction_view.name))

        self.assertEqual(len(sender.commit_syncer.sent), 1)
        receiver.receive_commit_sync(sender.commit_syncer.sent[0])

        self.assertEqual(receiver._transaction_view.focused_row()[1], self.txids[3])
        self.assertEqual(receiver._transaction_view.top_visible_row_key(), self.txids[3])

    def test_inbound_visible_transaction_row_still_aligns_to_top(self):
        top = self.make_top(size=(120, 8))

        top.receive_commit_sync(
            CommitSyncState(
                sync_index=40,
                cycles_per_char=Fraction(1, 1),
                expand_rows=False,
                chars_rel_to_start=0,
                mode="transaction_row",
            )
        )

        self.assertEqual(top._transaction_view.top_visible_row_key(), self.txids[3])
