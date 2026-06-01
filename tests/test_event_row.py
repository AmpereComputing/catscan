# Copyright (c) 2024-2025 Ampere Computing. All rights reserved.
# SPDX-License-Identifier: BSD-3-Clause

from perf_streams.event_stream import EventStreamWriter
from test_data import CatscanDataTest

from catscan.events import trace_events
from catscan.widgets.event_row import SUMMARY_BLOCK_SIZE, cycle_events


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
