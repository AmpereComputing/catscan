# Copyright (c) 2026 Ampere Computing. All rights reserved.
# SPDX-License-Identifier: BSD-3-Clause

from fractions import Fraction
from types import SimpleNamespace

from perf_streams.event_stream import EventStreamWriter
from test_data import CatscanDataTest

from catscan.commit_sync import CommitSyncer, CommitSyncState
from catscan.data import DataView, get_event_data
from catscan.events import trace_events
from catscan.events.mapping import Mapper
from catscan.state import CatscanState, HashableFrozenDict, Selection
from catscan.widgets.resource_view import ResourceView
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
    def __init__(
        self,
        *,
        my_pushout_index=None,
        other_commit_index=None,
        other_pushout_index=None,
        view_mode=DataView.TRANSACTIONS,
        other_view_mode=DataView.RESOURCE,
    ):
        self.syncing = True
        self.my = SimpleNamespace(view_mode=view_mode, pushout_index=my_pushout_index or {})
        self.other = SimpleNamespace(
            commit_index=other_commit_index or {},
            pushout_index=other_pushout_index or {},
            column_header_width=0,
            view_mode=other_view_mode,
        )
        self.sent = []
        self.failure_message = None
        self._last_sent_sync_state = None
        self._last_sent_anchor_state = None

    @property
    def view_mode(self):
        return self.my.view_mode

    @property
    def other_view_mode(self):
        return self.other.view_mode

    @property
    def other_column_header_width(self):
        return self.other.column_header_width

    def other_has_sync_index(self, sync_index):
        return sync_index in self.other.commit_index or sync_index in self.other.pushout_index

    def first_other_sync_index(self, candidates):
        for sync_index in candidates:
            if self.other_has_sync_index(sync_index):
                return sync_index
        return None

    def compute_commit_pushout_movements(self):
        return CommitSyncer.compute_commit_pushout_movements(self)

    def _sent_anchor_matches(self, sync_state):
        return CommitSyncer._sent_anchor_matches(self, sync_state)

    def _delta_sync_state(self, sync_state):
        return CommitSyncer._delta_sync_state(self, sync_state)

    def send(self, sync_state):
        self.sent.append(sync_state)
        CommitSyncer._remember_sent(self, sync_state)
        return True

    def _sync_state_matches(self, sync_state, previous_sync_state, field_names=None):
        return CommitSyncer._sync_state_matches(self, sync_state, previous_sync_state, field_names)

    def sent_anchor_matches(self, sync_state):
        return self._sent_anchor_matches(sync_state)

    def send_if_changed(self, sync_state):
        delta_sync_state = self._delta_sync_state(sync_state)
        if not delta_sync_state.has_fields():
            if sync_state.sync_index is None:
                self._last_sent_anchor_state = None
            return False
        self.sent.append(delta_sync_state)
        CommitSyncer._remember_sent(self, sync_state)
        return True


class DummyMainLoop:
    def draw_screen(self):
        pass


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

    def make_top(self, size=(120, 4), view=DataView.TRANSACTIONS, start_commit_sync=True):
        top = Top(Args(view=view))
        top.cached_maxcol = 120
        stream_data = self.load_resource_event_data() if view == DataView.RESOURCE else self.esd
        top.update_stream_data(stream_data)
        top.render(size, focus=True)
        if start_commit_sync:
            commit_syncer = DummyCommitSyncer(view_mode=view, other_commit_index=top.commit_sync_index)
            for event_view in top._event_views():
                event_view.start_commit_sync(commit_syncer, top.commit_sync_event, top.commit_sync_data_name)
        return top

    def make_transaction_view(self, callback=None, focus_callback=None):
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

    def make_resource_view(self, view_type=ResourceView, **kwargs):
        events = []
        view = view_type(
            "resource",
            self.make_state(),
            self.load_resource_event_data(),
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
