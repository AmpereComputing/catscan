# Copyright (c) 2024-2026 Ampere Computing. All rights reserved.
# SPDX-License-Identifier: BSD-3-Clause

from perf_streams.event_stream import EventStreamWriter
from test_data import CatscanDataTest

from catscan.events import trace_events
from catscan.search import *
from catscan.util import hex_args_to_re


class TestSearch(CatscanDataTest):
    @classmethod
    def event_stream_setup(cls):
        writer = EventStreamWriter(cls.test_filename)
        btb_lookup = writer.define_event("core.btb.lookup", "The BTB is looked up")
        btb_hit = writer.define_data("core.btb.hit", "Whether the BTB lookup is a hit or miss")
        predictor = writer.define_data("predictor", "name of the predictor used")
        icache_lookup = writer.define_event("core.icache.lookup", "The icache is looked up")
        icache_hit = writer.define_data("core.icache.hit", "Whether the icache lookup is a hit or miss")
        inst_exec = writer.define_event("core.instruction_exec", "An instruction is exec")
        inst_commit = writer.define_event("core.instruction_commit", "An instruction is committed")
        inum = writer.define_data("core.inum", "The committed instruction's stable index")
        program_counter = writer.define_data("program_counter", "An instruction's program counter")
        prev_program_counter = writer.define_data("prev_program_counter", "An instruction's program counter")

        writer.start_simulation()
        next_inum = 5
        last_pc = 0x3FFFC
        next_pc = 0x40000

        # Record the times of the instruction exec and commit events with PC's of
        # 0x4001C
        cls.exact_match_instruction_times = []
        cls.filtered_match_instruction_times = []

        for time in range(10000, 20000, 500):
            fetch_tx = writer.begin_transaction(time=time)
            writer.post_event(
                btb_lookup,
                time=time,
                transaction=fetch_tx,
                values={
                    predictor: "main_btb",
                    btb_hit: 0 if time == 15000 else 1,
                },
            )
            writer.post_event(
                icache_lookup,
                time=time + 100,
                transaction=fetch_tx,
                values={
                    icache_hit: 0 if time == 15000 else 1,
                },
            )
            for inst_time in range(time + 200, time + 400, 25):
                inst_tx = writer.begin_transaction(time=inst_time, parent=fetch_tx)
                writer.post_event(
                    inst_exec,
                    time=inst_time,
                    transaction=inst_tx,
                    values={
                        inum: next_inum,
                        program_counter: next_pc,
                        prev_program_counter: last_pc,
                    },
                )
                writer.post_event(
                    inst_commit,
                    time=inst_time + 2,
                    transaction=inst_tx,
                    values={
                        inum: next_inum,
                        program_counter: next_pc,
                        prev_program_counter: last_pc,
                    },
                )
                if next_pc == 0x4001C:
                    cls.exact_match_instruction_times.append(inst_time)
                    cls.exact_match_instruction_times.append(inst_time + 2)
                if next_pc in {0x40018, 0x4001C}:
                    cls.filtered_match_instruction_times.append(inst_time + 2)
                last_pc = next_pc
                next_inum += 1
                next_pc = next_pc + 4 if next_pc < 0x40020 else 0x40010
        writer.close()

        events = [
            "core.btb.lookup",
            "core.icache.lookup",
            "core.instruction_exec",
            "core.instruction_commit",
        ]
        cls.set_event_stream_params(events=[trace_events.trace_spec(e) for e in events])

    def test_get_search_fields(self):
        inum_fields = FilteringSearcher.get_search_fields(self.esd, ["*.inum"])
        self.assertEqual(inum_fields, {"core.inum"})

        hit_fields = FilteringSearcher.get_search_fields(self.esd, ["*.hit"])
        self.assertEqual(hit_fields, {"core.icache.hit", "core.btb.hit"})

    def test_basic_text_search(self):
        main_btb_searcher = TextSearcher("main_btb", None, None, False, hex_args_to_re(["program_counter"]))

        # Ensure we're told to search all fields and all rows
        self.assertFalse(main_btb_searcher.filtering_fields())
        self.assertTrue(main_btb_searcher.search_row("foobar", []))
        self.assertTrue(main_btb_searcher.search_row("core.instruction_commit", []))

        btb_access = list(self.esd.event_rows["core.btb.lookup"][11500])[0]
        icache_access = list(self.esd.event_rows["core.icache.lookup"][11600])[0]

        self.assertTrue(main_btb_searcher.match(btb_access))
        self.assertFalse(main_btb_searcher.match(icache_access))

    def test_basic_text_search_hex_number(self):
        full_address_searcher = TextSearcher("0x4001C", None, None, False, hex_args_to_re(["program_counter"]))
        partial_address_searcher = TextSearcher("0x4001", None, None, False, hex_args_to_re(["program_counter"]))

        inst_a = list(self.esd.event_rows["core.instruction_commit"][10227])[0]  # pc = 0x40004
        inst_b = list(self.esd.event_rows["core.instruction_commit"][10302])[0]  # pc = 0x40010
        inst_c = list(self.esd.event_rows["core.instruction_commit"][10377])[0]  # pc = 0x4001C

        # Only C matches, because its program_counter is 0x4001C exactly
        self.assertFalse(full_address_searcher.match(inst_a))
        self.assertFalse(full_address_searcher.match(inst_b))
        self.assertTrue(full_address_searcher.match(inst_c))

        # Doing a partial string search allows both B and C to match, because
        # they share the prefix 0x4001
        self.assertFalse(partial_address_searcher.match(inst_a))
        self.assertTrue(partial_address_searcher.match(inst_b))
        self.assertTrue(partial_address_searcher.match(inst_c))

    def test_integer_search(self):
        address_searcher = IntSearcher(0x4001C, None, None, None)
        masked_address_searcher = IntSearcher(0x4001C, None, None, 0xFFFF0)

        inst_a = list(self.esd.event_rows["core.instruction_commit"][10227])[0]  # pc = 0x40004
        inst_b = list(self.esd.event_rows["core.instruction_commit"][10302])[0]  # pc = 0x40010
        inst_c = list(self.esd.event_rows["core.instruction_commit"][10377])[0]  # pc = 0x4001C

        # Only C matches, because its program_counter is 0x4001C exactly
        self.assertFalse(address_searcher.match(inst_a))
        self.assertFalse(address_searcher.match(inst_b))
        self.assertTrue(address_searcher.match(inst_c))

        # Doing a partial string search allows both B and C to match, because
        # they share the prefix 0x4001
        self.assertFalse(masked_address_searcher.match(inst_a))
        self.assertTrue(masked_address_searcher.match(inst_b))
        self.assertTrue(masked_address_searcher.match(inst_c))

    def test_text_search_tracking(self):
        searcher = TextSearcher("0x4001C", None, None, False, hex_args_to_re(["program_counter"]))

        # Start *just* after the first matching event
        starting_event = list(self.esd.event_rows["core.instruction_commit"][10702])[0]

        data_search = EventStreamDataSearch(self.esd.events(), searcher, starting_event)
        expected_matches = self.__class__.exact_match_instruction_times

        self.assertEqual(data_search.total_matches, len(expected_matches))

        # The 'next' match is the third overall match (0-indexed)
        self.assertEqual(data_search.cursor_idx, 2)
        self.assertEqual(data_search.search_cursor.time, expected_matches[2])

        # Advance the search cursor through each potential match

        # Force a wrap-around by advancing the search cursor past the last event
        for i in range(3, len(expected_matches)):
            next_match = data_search.next()
            self.assertEqual(data_search.cursor_idx, i)
            self.assertEqual(next_match.time, expected_matches[i])

        next_match = data_search.next()
        # We should now be at the beginning
        self.assertEqual(data_search.cursor_idx, 0)
        self.assertEqual(next_match.time, expected_matches[0])

        prev_match = data_search.prev()

        # We should now be at the end
        self.assertEqual(data_search.cursor_idx, len(expected_matches) - 1)
        self.assertEqual(prev_match.time, expected_matches[-1])

    def test_filtered_integer_search_tracking(self):
        # search for instruction commits with PCs 0x40018 or 0x4001C.
        # Furthermore, this is filtered by both row and field name to
        # test avoiding matching on other events (core.instruction_exec)
        searcher = IntSearcher(
            0x4001C,
            search_rows=["core.instruction_commit"],
            search_fields=["program_counter"],
            search_mask=0xFFFFFFFFFFFFFFF8,
        )
        starting_commit = list(self.esd.event_rows["core.instruction_commit"][10802])[0]

        data_search = EventStreamDataSearch(self.esd.events(), searcher, starting_commit)

        expected_matches = self.__class__.filtered_match_instruction_times

        self.assertEqual(data_search.total_matches, len(expected_matches))

        # The 'next' match (which is the same as the starting point) is the
        # fourth overall match (0-indexed)
        self.assertEqual(data_search.search_cursor.time, 10802)
        self.assertEqual(data_search.cursor_idx, 3)

        # Look through 'previous' matches
        data_search.prev()
        data_search.prev()
        prev_match = data_search.prev()

        self.assertEqual(data_search.search_cursor.time, 10352)
        self.assertEqual(prev_match.time, 10352)
        self.assertEqual(data_search.cursor_idx, 0)

        # Wrap around to the end
        prev_match = data_search.prev()

        self.assertEqual(data_search.search_cursor.time, 19827)
        self.assertEqual(prev_match.time, 19827)
        self.assertEqual(data_search.cursor_idx, len(expected_matches) - 1)


class TestPerPeriodSearch(CatscanDataTest):
    @classmethod
    def event_stream_setup(cls):
        writer = EventStreamWriter(cls.test_filename)
        fetch = writer.define_event("core.fetch", "An instruction fetch is performed")
        program_counter = writer.define_data("program_counter", "An instruction's program counter")

        writer.start_simulation()

        # a list of times of fetches made in the generated event stream. They
        # are grouped such that all fetches in a group of 4 'aligned' times
        # (i.e. 0-3, 4-7, 8-11, 12-15) are grouped together for test_any()
        # below.
        cls.test_fetches = [
            [0, 1, 2, 3],
            [4, 4, 5, 5, 5],
            [8, 9, 11],
            [12],
        ]

        pc = 0x64A88C0
        for fetch_grouping in cls.test_fetches:
            for fetch_time in fetch_grouping:
                writer.post_event(fetch, time=fetch_time, values={program_counter: pc})
            pc += 4
        writer.close()

        cls.set_event_stream_params(events=[trace_events.trace_spec("core.fetch")])

    def test_any(self):
        all_searcher = TextSearcher("*", None, None, False, hex_args_to_re(["program_counter"]))
        searcher = PerPeriodSearcher(4, all_searcher)

        self.assertTrue(searcher.search_row("core.fetch", []))

        last_fetch_time = -1
        group_index = 0
        time_index = 0
        for fetch_grouping in self.__class__.test_fetches:
            group_index = 0
            for fetch_time in fetch_grouping:
                # Update the indices such that 'group_index' always refers to
                # the index of the current fetch relative to its group (i.e. 4
                # times), while 'time_index' refers to the index of the current
                # fetch relative to its precise time.
                if last_fetch_time != fetch_time:
                    last_fetch_time = fetch_time
                    time_index = 0
                else:
                    group_index += 1
                    time_index += 1

                self.assertEqual(
                    searcher.match(list(self.esd.event_rows["core.fetch"][fetch_time])[time_index]), group_index >= 4
                )
