# Copyright (c) 2024-2025 Ampere Computing. All rights reserved.
# SPDX-License-Identifier: BSD-3-Clause

from fractions import Fraction

from perf_streams.event_stream import EventStreamWriter
from test_data import CatscanDataTest

from catscan.data import DataView, get_event_data
from catscan.events import trace_events
from catscan.events.mapping import Mapper
from catscan.state import CatscanState, HashableFrozenDict, Selection
from catscan.widgets.event_row import SUMMARY_BLOCK_SIZE, EventRow, cycle_events
from catscan.widgets.event_view import LazyEventListBox, LazyEventListWalker
from catscan.widgets.transaction_view import TransactionView


class TestEventRow(CatscanDataTest):
    PS_PER_CYCLE = 333
    # The event stream contains flushes at 250083ps, 428904ps, 437229ps,
    # 444555ps, 468531ps, and 518148ps
    FLUSH_TIMES_PS = [250083, 428904, 437229, 444555, 468531, 518148]

    @classmethod
    def event_stream_setup(cls):
        writer = EventStreamWriter(cls.test_filename)
        flush = writer.define_event("flush", "micro-architectural flush")
        writer.start_simulation()
        for ps in cls.FLUSH_TIMES_PS:
            writer.post_event(flush, time=ps)
        writer.close()

        events = [
            trace_events.trace_spec("flush"),
        ]
        cls.set_event_stream_params(events=events)

    def test_unaligned_cycle_events(self):
        """
        Test that `cycle_events()` handles returning results properly when the
        requested time range is *not* already 'aligned'
        """
        flush_event_list = cycle_events(
            self.esd.event_rows["flush"],
            ps_per_cycle=self.__class__.PS_PER_CYCLE,
            start_ps=250083,
            desired_datapoints=806,
        )

        self.assertEqual(len(flush_event_list), 806)

        for ps in self.__class__.FLUSH_TIMES_PS:
            self.assertEqual(len(flush_event_list[(ps - 250083) // self.__class__.PS_PER_CYCLE]), 1)
        self.assertEqual(sum([len(c) for c in flush_event_list]), 6)

    def test_aligned_cycle_events(self):
        """
        Test that `cycle_events()` handles returning results properly when the
        requested time range is already 'aligned'
        """
        start_ps = 250083 - (250083 % (self.__class__.PS_PER_CYCLE * SUMMARY_BLOCK_SIZE))
        flush_event_list = cycle_events(
            self.esd.event_rows["flush"],
            ps_per_cycle=self.__class__.PS_PER_CYCLE,
            start_ps=start_ps,
            desired_datapoints=1200,
        )

        self.assertEqual(len(flush_event_list), 1200)

        for ps in self.__class__.FLUSH_TIMES_PS:
            self.assertEqual(len(flush_event_list[(ps - start_ps) // self.__class__.PS_PER_CYCLE]), 1)
        self.assertEqual(sum([len(c) for c in flush_event_list]), 6)
    def _state(
        self,
        expand_rows: bool,
        cycles_per_char: Fraction = Fraction(1, 1),
        start_ps: int | None = None,
    ) -> CatscanState:
        return CatscanState(
            has_focus=True,
            loading=False,
            ps_per_cycle=self.__class__.PS_PER_CYCLE,
            column_header_width=8,
            cycles_per_char=cycles_per_char,
            start_ps=start_ps if start_ps is not None else self.__class__.FLUSH_TIMES_PS[0],
            start_row=1,
            expand_rows=expand_rows,
            selection=Selection(),
            sort_event_keys=True,
            highlighted_transactions=HashableFrozenDict(),
            marked_events=HashableFrozenDict(),
            searcher=None,
            messages=[],
            show_help=False,
        )

    def _event_row(self, state: CatscanState) -> EventRow:
        row = EventRow(
            self.esd.event_rows["flush"],
            state,
            0,
            on_make_selection=lambda *_args, **_kwargs: None,
            on_extend_selection=lambda *_args, **_kwargs: None,
        )
        row.render((80,), False)
        return row

    def test_hover_expanded_visible_event(self):
        state = self._state(expand_rows=True)
        row = self._event_row(state)

        hover = row.mouse_to_hover(state.column_header_width, 0)

        self.assertTrue(hover.is_event())
        self.assertEqual(self.__class__.FLUSH_TIMES_PS[0], hover.event.time)

    def test_hover_collapsed_single_event_cell_returns_event(self):
        state = self._state(expand_rows=False)
        row = self._event_row(state)

        hover = row.mouse_to_hover(state.column_header_width, 0)

        self.assertTrue(hover.is_event())
        self.assertEqual(self.__class__.FLUSH_TIMES_PS[0], hover.event.time)

    def test_hover_expanded_aggregate_cell_returns_time_range(self):
        state = self._state(expand_rows=True, cycles_per_char=Fraction(2048, 1), start_ps=0)
        row = self._event_row(state)

        hover = row.mouse_to_hover(state.column_header_width, 0)

        self.assertTrue(hover.is_time_range())
        self.assertFalse(hover.is_event())
        self.assertEqual(0, hover.start_ps)
        self.assertEqual(2048 * self.__class__.PS_PER_CYCLE, hover.end_ps)

    def test_hover_zoomed_in_single_event_spans_multiple_columns(self):
        state = self._state(expand_rows=False, cycles_per_char=Fraction(1, 8))
        row = self._event_row(state)

        for offset in range(8):
            hover = row.mouse_to_hover(state.column_header_width + offset, 0)
            self.assertTrue(hover.is_event())
            self.assertEqual(self.__class__.FLUSH_TIMES_PS[0], hover.event.time)

        self.assertFalse(row.mouse_to_hover(state.column_header_width + 8, 0))

    def test_hover_empty_and_header_positions(self):
        state = self._state(expand_rows=True)
        row = self._event_row(state)

        self.assertFalse(row.mouse_to_hover(state.column_header_width - 1, 0))
        self.assertFalse(row.mouse_to_hover(state.column_header_width, 1))
        self.assertFalse(row.mouse_to_hover(state.column_header_width + 1, 0))


class TestExpandedLazyTransactionView(CatscanDataTest):
    PS_PER_CYCLE = 100
    TRANSACTION_ROWS = 600
    EVENTS_PER_TIME = 4

    @classmethod
    def event_stream_setup(cls):
        writer = EventStreamWriter(cls.test_filename)
        work = writer.define_event("work", "transaction work")
        writer.start_simulation()

        for row in range(cls.TRANSACTION_ROWS):
            transaction = writer.begin_transaction(time=row * cls.PS_PER_CYCLE)
            for _ in range(cls.EVENTS_PER_TIME):
                writer.post_event(work, time=row * cls.PS_PER_CYCLE, transaction=transaction)
            writer.end_transaction(transaction, time=(row + 1) * cls.PS_PER_CYCLE)

        writer.close()
        cls.set_event_stream_params(events=[trace_events.trace_spec("work")])

    def load_event_data(self):
        return get_event_data(
            self.test_filename,
            DataView.TRANSACTIONS,
            mapper=Mapper([], [], []),
            event_filters=self.event_filters,
            events=list(self.events),
            post_to_tx=[],
            pull_from_tx=[],
            occupancy=[],
            pct_loaded_callback=lambda _pct: None,
            transaction_name="txid",
            transaction_start=trace_events.trace_spec("work"),
            transaction_end=None,
        )

    def state(self):
        return CatscanState(
            has_focus=True,
            loading=False,
            ps_per_cycle=self.PS_PER_CYCLE,
            column_header_width=12,
            cycles_per_char=Fraction(1, 1),
            start_ps=0,
            start_row=1,
            expand_rows=True,
            selection=Selection(),
            sort_event_keys=True,
            highlighted_transactions=HashableFrozenDict(),
            marked_events=HashableFrozenDict(),
            searcher=None,
            messages=[],
            show_help=False,
        )

    def test_lazy_expanded_rows_report_logical_and_visual_sizes(self):
        view = TransactionView("transaction", self.state(), self.esd, *(lambda *args: False for _ in range(8)))

        self.assertIsInstance(view.list_walker, LazyEventListWalker)
        self.assertIsInstance(view.list_box, LazyEventListBox)
        self.assertEqual(self.TRANSACTION_ROWS, len(view.list_walker))
        self.assertEqual(self.TRANSACTION_ROWS * self.EVENTS_PER_TIME, view.list_box.rows_max((134, 73)))

    def test_lazy_expanded_rows_render_near_end_at_box_size(self):
        view = TransactionView("transaction", self.state(), self.esd, *(lambda *args: False for _ in range(8)))
        view.list_walker.set_focus(self.TRANSACTION_ROWS - 1)

        canvas = view.render((134, 73), focus=True)

        self.assertEqual((134, 73), (canvas.cols(), canvas.rows()))

    def test_lazy_expanded_rows_scroll_bottom_uses_logical_focus_position(self):
        view = TransactionView("transaction", self.state(), self.esd, *(lambda *args: False for _ in range(8)))

        self.assertIsNone(view.keypress((134, 73), "G"))

        self.assertEqual(self.TRANSACTION_ROWS - 1, view.list_box.focus_position)
