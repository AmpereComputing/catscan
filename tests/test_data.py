# Copyright (c) 2024-2026 Ampere Computing. All rights reserved.
# SPDX-License-Identifier: BSD-3-Clause

import os
import unittest
from collections import deque
from collections.abc import Callable
from tempfile import TemporaryDirectory

from perf_streams.event_stream import Event, EventStreamWriter

from catscan.data import get_event_data
from catscan.events import EventSpecification, matching_spec, rename_spec, trace_events
from catscan.events.mapping import Mapper


class CatscanDataTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.test_dir = TemporaryDirectory()
        cls.test_filename = os.path.join(cls.test_dir.name, "catscan_data_test.es")
        cls.event_stream_setup()

    @classmethod
    def event_stream_setup(cls) -> None:
        """Subclasses must override event_stream_setup to create the event stream they will later read in."""
        raise RuntimeError("CatscanDataTest.event_stream_setup must be overridden in subclasses")

    @classmethod
    def tearDownClass(cls):
        cls.test_dir.cleanup()
        del cls.test_dir

    @classmethod
    def set_event_stream_params(
        cls,
        *,
        events: list[EventSpecification] | None = None,
        splits: list[tuple[EventSpecification, Callable[[Event], None]]] | None = None,
        renames: list[Callable[[str], str]] | None = None,
        excludes: list[EventSpecification] | None = None,
        inst_args: list[str] | None = None,
        post_to_tx: list[str] | None = None,
        pull_from_tx: list[str] | None = None,
        occupancy: list[str] | None = None,
    ):
        cls.inst_args = inst_args or []
        cls.events = events or []

        cls.splits = splits  # None allowed
        cls.renames = renames or []
        cls.excludes = excludes or []

        cls.post_to_tx = post_to_tx or []
        cls.pull_from_tx = pull_from_tx or []
        cls.occupancy = occupancy or []

        cls.event_filters = trace_events.EventFilters(
            [
                trace_events.EventSplitter(cls.splits),
                trace_events.EventRenamer(cls.renames),
                trace_events.EventIncluder(cls.events),
                trace_events.EventExcluder(cls.excludes),
            ],
        )

    def setUp(self):
        self.esd = self.load_event_data()

    def load_event_data(self):
        self._pct_loaded = 0

        def pct_loaded_callback():
            def handle(pct):
                self._pct_loaded = pct

            return handle

        mapper = Mapper([], [], self.inst_args)

        return get_event_data(
            self.test_filename,
            "resource",
            mapper=mapper,
            event_filters=self.event_filters,
            events=self.events,
            post_to_tx=self.post_to_tx,
            pull_from_tx=self.pull_from_tx,
            occupancy=self.occupancy,
            pct_loaded_callback=pct_loaded_callback(),
        )


class TestData(CatscanDataTest):
    @classmethod
    def event_stream_setup(cls):
        writer = EventStreamWriter(cls.test_filename)
        predict = writer.define_event("core.predict", "A branch prediction is made")
        fetch = writer.define_event("core.fetch", "A prediction's instruction is fetched")
        lookup = writer.define_event("core.lookup", "A instruction is looked up in cache structure")
        decode = writer.define_event("core.decode", "An instruction is decoded")
        issue = writer.define_event("core.issue", "An operation is issued")
        execute = writer.define_event("core.exec", "An operation is executed")
        commit = writer.define_event("core.commit", "An instruction is committed")
        allocate = writer.define_event("core.allocate", "Instruction resource allocated")
        deallocate = writer.define_event("core.deallocate", "Instruction resource deallocated")
        inum = writer.define_data("core.inum", "A committed instruction's stable index")
        program_counter = writer.define_data("program_counter", "An instruction's program counter")
        hit = writer.define_data("core.hit", "Hit in cache structure")
        buffer_id = writer.define_data("core.buffer_id", "ID in buffer structure")

        writer.start_simulation()

        cls.PS_PER_CYCLE = 300
        cls.MACHINE_WIDTH = 4
        cls.predict_cycles = {1, 2, 3, 20, 21, 32, 45, 46, 47, 48, 50}
        cls.instructions_per_prediction = {
            1: 2,
            2: 4,
            3: 5,
            20: 8,
            21: 6,
            32: 4,
            45: 7,
            46: 3,
            47: 4,
            48: 2,
            50: 3,
        }
        cls.ops_per_instruction = {
            11: 2,
            16: 3,
            27: 2,
            31: 4,
            45: 2,
        }

        fetches = deque()
        instructions = deque()
        ops = deque()

        next_inum = 0
        next_pc = 0x40000
        inst_tx_to_inum = {}
        inst_tx_to_buffer_id = {}

        def issued(op: Event) -> bool:
            return any(e.definition_id == issue.id for e in op.events)

        def fetched(fetch: Event) -> bool:
            return any(e.definition_id == fetch.id for e in fetch.events)

        def decoded(instruction: Event) -> bool:
            return any(e.definition_id == decode.id for e in instruction.events)

        def ready_to_commit(instruction: Event) -> bool:
            return decoded(instruction) and all(
                any(e.definition_id == execute.id for e in c.events) for c in instruction.children
            )

        for cycle in range(70):
            time = cycle * cls.PS_PER_CYCLE

            # commit any fully-executed instructions
            while instructions and ready_to_commit(instructions[0]):
                instruction = instructions.popleft()
                writer.post_event(
                    commit, time=time, transaction=instruction, values={inum: inst_tx_to_inum.pop(instruction.txid)}
                )
                writer.post_event(
                    deallocate,
                    time=time,
                    transaction=instruction,
                    values={buffer_id: inst_tx_to_buffer_id.pop(instruction.txid)},
                )
                writer.end_transaction(instruction, time=time)

            # execute up to MACHINE_WIDTH issued operations
            executed_ops = 0
            while ops and issued(ops[0]) and executed_ops < cls.MACHINE_WIDTH:
                executed_ops += 1
                op = ops.popleft()
                writer.post_event(execute, time=time, transaction=op)
                writer.end_transaction(op, time=time)

            # issue up to MACHINE_WIDTH decoded operations
            issued_ops = 0
            for op in ops:
                if not issued(op):
                    writer.post_event(issue, time=time, transaction=op)
                    issued_ops += 1
                if issued_ops >= cls.MACHINE_WIDTH:
                    break

            # decode up to MACHINE_WIDTH fetched instructions
            decoded_ops = 0
            for instruction in instructions:
                ops_per_instruction = cls.ops_per_instruction.get(inst_tx_to_inum[instruction.txid], 1)
                if (decoded_ops + ops_per_instruction) > cls.MACHINE_WIDTH:
                    break

                if not decoded(instruction):
                    instruction_buffer_id = list(set(range(100)) - set(inst_tx_to_buffer_id.values()))[0]
                    writer.post_event(
                        allocate, time=time, transaction=instruction, values={buffer_id: instruction_buffer_id}
                    )
                    inst_tx_to_buffer_id[instruction.txid] = instruction_buffer_id

                    for _ in range(ops_per_instruction):
                        writer.post_event(
                            decode, time=time, transaction=instruction, values={inum: inst_tx_to_inum[instruction.txid]}
                        )
                        op = writer.begin_transaction(time=time, parent=instruction)
                        ops.append(op)

                    decoded_ops += ops_per_instruction

            # fetch a prediction, if available
            if fetches:
                fetch_tx = fetches.popleft()
                predict_event = fetch_tx.events[0]
                predict_cycle = predict_event.time / cls.PS_PER_CYCLE
                instructions_in_fetch = cls.instructions_per_prediction[predict_cycle]
                for _ in range(instructions_in_fetch):
                    instruction = writer.begin_transaction(time=time, parent=fetch_tx)
                    instructions.append(instruction)
                    inst_tx_to_inum[instruction.txid] = next_inum
                    writer.post_event(
                        fetch, time=time, transaction=instruction, values={program_counter: next_pc, inum: next_inum}
                    )
                    writer.post_event(
                        lookup,
                        time=time,
                        transaction=instruction,
                        values={hit: 1 if cycle % 5 != 0 else 0},
                    )
                    next_pc += 4
                    next_inum += 1

                writer.end_transaction(fetch_tx, time=time)

            # predict any predictions supposed to start this cycle
            if cycle in cls.predict_cycles:
                fetch_tx = writer.begin_transaction(time=time)
                writer.post_event(predict, time=time, transaction=fetch_tx)
                fetches.append(fetch_tx)

        writer.close()

        events = [trace_events.trace_spec("core.predict")]

        # Note: The splits are intentionally placed in the middle of the
        # definitions of the other events so that we can test that the order of
        # initialization of the EventSpecification objects for each determines
        # the eventual order of the event rows
        splits = [trace_events.split_spec("core.lookup/hit=fetch_hit_{hit}")]
        renames = [rename_spec("fetch_hit_1=fetch_hit")]
        excludes = [trace_events.trace_spec("fetch_hit_[0]")]

        post_to_tx = ["core.fetch:program_counter"]
        pull_from_tx = ["core.commit:program_counter"]

        event_names = ["core.fetch", "core.decode", "core.issue", "core.exec", "core.commit"]
        events += [trace_events.trace_spec(e) for e in event_names]
        cls.set_event_stream_params(
            events=events,
            splits=splits,
            renames=renames,
            excludes=excludes,
            post_to_tx=post_to_tx,
            pull_from_tx=pull_from_tx,
            occupancy=["instruction_queue:core.allocate,core.deallocate"],
        )

    def test_pct_callback(self):
        # Check we are reporting file completion
        self.assertAlmostEqual(self._pct_loaded, 100, delta=5)

    def test_first_last_time(self):
        # Check the overall first/last times were correctly discovered
        self.assertEqual(self.esd.first_time, 300)  # first core.predict
        self.assertEqual(self.esd.last_time, 16800)  # final core.commit

    def test_transactions(self):
        # Check the transactions are built up correctly
        # txid=71 is an instruction
        txid = 71
        self.assertIn(txid, self.esd.transactions)

        txn = self.esd.transactions[txid]
        self.assertEqual(txn.start_time, 13800)
        self.assertEqual(txn.end_time, 15300)

        # four children: 4 ops (83, 84, 85, 86)
        self.assertEqual(txn.children, {83, 84, 85, 86})
        # one parent, the fetch (68)
        self.assertEqual(txn.parents, {68})

    def test_event_counts(self):
        # Check per-event counts are accurate
        self.assertEqual(self.esd.event_rows["core.commit"].event_count, 48)
        self.assertEqual(len(self.esd.event_rows["core.commit"]), 48)

        # Check overall event count is accurate
        self.assertEqual(self.esd.event_count, 419)

    def test_max_events_per_time(self):
        # Check max_events_per_time() on the event rows
        self.assertEqual(self.esd.event_rows["core.predict"].max_events_per_time(), 1)
        self.assertEqual(
            self.esd.event_rows["core.fetch"].max_events_per_time(),
            max(self.instructions_per_prediction.values()),
        )
        self.assertEqual(self.esd.event_rows["core.decode"].max_events_per_time(), self.MACHINE_WIDTH)
        self.assertEqual(self.esd.event_rows["core.issue"].max_events_per_time(), self.MACHINE_WIDTH)

    def test_event_range_simple(self):
        inst_commit = self.esd.event_rows["core.commit"]

        # Check finding single event, excluding end
        found = list(inst_commit[2700:7500])
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0].time, 2700)

    def test_event_range_skipping(self):
        inst_commit = self.esd.event_rows["core.commit"]

        # Check stepping (skipping) some events
        found = list(inst_commit[2700:7800:2])
        self.assertEqual(len(found), 2)
        self.assertEqual(found[0].time, 7500)
        self.assertEqual(found[0].data["core.inum"], 11)
        self.assertEqual(found[1].time, 7500)
        self.assertEqual(found[1].data["core.inum"], 13)

    def test_event_range_skipping_reverse(self):
        inst_commit = self.esd.event_rows["core.commit"]

        # Check stepping (skipping) some events in reverse
        found = list(inst_commit[7500:2500:-2])
        self.assertEqual(len(found), 2)
        self.assertEqual(found[0].time, 7500)
        self.assertEqual(found[0].data["core.inum"], 12)
        self.assertEqual(found[1].time, 2700)
        self.assertEqual(found[1].data["core.inum"], 10)

    def test_single_event_time(self):
        inst_commit = self.esd.event_rows["core.commit"]

        # Check indexing by a single event start time
        found = list(inst_commit[15900])
        self.assertEqual(len(found), 4)
        for index, op in enumerate(found):
            self.assertEqual(op.time, 15900)
            self.assertEqual(op.data["core.inum"], 36 + index)

    def test_younger_older(self):
        uop_issue = self.esd.event_rows["core.issue"]

        uop_a = list(uop_issue[10500])[0]
        uop_b = list(uop_issue[10500])[1]
        uop_c = list(uop_issue[10500])[2]
        uop_d = list(uop_issue[10500])[3]
        uop_e = list(uop_issue[10800])[0]
        uop_f = list(uop_issue[14400])[0]
        uop_g = list(uop_issue[14400])[1]

        # Ensure we have the expected uop to start
        self.assertEqual(uop_a.data["txid"], 63)
        self.assertEqual(uop_b.data["txid"], 64)
        self.assertEqual(uop_c.data["txid"], 65)
        self.assertEqual(uop_d.data["txid"], 66)
        self.assertEqual(uop_e.data["txid"], 67)
        self.assertEqual(uop_f.data["txid"], 77)
        self.assertEqual(uop_g.data["txid"], 78)

        self.assertEqual(uop_issue.oldest_younger(uop_a), uop_b)
        self.assertEqual(uop_issue.oldest_younger(uop_b), uop_c)
        self.assertEqual(uop_issue.oldest_younger(uop_c), uop_d)
        self.assertEqual(uop_issue.oldest_younger(uop_d), uop_e)
        self.assertEqual(uop_issue.oldest_younger(uop_f), uop_g)

        self.assertEqual(uop_issue.youngest_older(uop_b), uop_a)
        self.assertEqual(uop_issue.youngest_older(uop_c), uop_b)
        self.assertEqual(uop_issue.youngest_older(uop_d), uop_c)
        self.assertEqual(uop_issue.youngest_older(uop_e), uop_d)
        self.assertEqual(uop_issue.youngest_older(uop_g), uop_f)

    def test_closest_to(self):
        inst_commit = self.esd.event_rows["core.commit"]

        def assertClosestTo(time, inst):
            self.assertEqual(inst_commit.closest_to(time).data["core.inum"], inst)

        # All of these should resolve to the first instruction
        assertClosestTo(-10, 0)
        assertClosestTo(1799, 0)
        assertClosestTo(1800, 0)

        # If the time is after the closest, we pick the last event in the cycle
        assertClosestTo(1801, 1)
        assertClosestTo(1949, 1)

        # Then we should switch to the next cycle/instruction once we pass the
        # midway point between them
        assertClosestTo(1950, 2)
        # If we are precisely on the same time as several events, we pick the
        # "middle" one
        assertClosestTo(2100, 3)

        # Check that this one "rounds down"
        assertClosestTo(16049, 39)

        # Check that last cycle's worth of instructions working right
        assertClosestTo(16501, 46)
        assertClosestTo(16800, 47)
        assertClosestTo(999999, 47)

    def test_ancestors(self):
        uop_issue = self.esd.event_rows["core.issue"]
        fetch_txid = 76
        instruction_txid = 79
        uop_txid = 98
        txid = uop_txid
        uop = list(uop_issue[15300])[0]
        self.assertEqual(uop.data["txid"], txid)

        sort_txns = lambda txns: sorted(txns, key=lambda txn: txn.txid)

        # Three ways to get ancestors
        event_ancestors = sort_txns(self.esd.ancestors(uop))
        txid_ancestors = sort_txns(self.esd.ancestors(txid))
        transaction_ancestors = sort_txns(self.esd.ancestors(self.esd.transactions[txid]))
        expected_ancestors = sort_txns([self.esd.transactions[t] for t in [fetch_txid, instruction_txid]])

        self.assertEqual(event_ancestors, expected_ancestors)
        self.assertEqual(txid_ancestors, expected_ancestors)
        self.assertEqual(transaction_ancestors, expected_ancestors)

    def test_descendants(self):
        predict = self.esd.event_rows["core.predict"]
        fetch_txid = 10
        instruction_txids = [15, 16, 17, 18, 19]
        uop_txids = [20, 21, 22, 23, 24]
        txid = fetch_txid
        fetch = list(predict[900])[0]
        self.assertEqual(fetch.data["txid"], txid)

        sort_txns = lambda txns: sorted(txns, key=lambda txn: txn.txid)
        event_descendants = sort_txns(self.esd.descendants(fetch))
        txid_descendants = sort_txns(self.esd.descendants(txid))
        transaction_descendants = sort_txns(self.esd.descendants(self.esd.transactions[txid]))
        expected_descendants = sort_txns([self.esd.transactions[t] for t in instruction_txids + uop_txids])

        self.assertEqual(event_descendants, expected_descendants)
        self.assertEqual(txid_descendants, expected_descendants)
        self.assertEqual(transaction_descendants, expected_descendants)

    def test_event_row_keys(self):
        expected_row_keys = (
            "core.predict",
            # Note: fetch_hit, a "split", should be placed here because it was
            # initialized in this order relative to the other events
            "fetch_hit",
            "core.fetch",
            "core.decode",
            "core.issue",
            "core.exec",
            "core.commit",
            "instruction_queue",
        )
        self.assertEqual(self.esd.event_row_keys, expected_row_keys)

    def test_post_to_pull_from_tx(self):
        # Note: program_counter is being pulled from core.fetch and
        # pushed to core.commit in the common setup code above.
        inst_commit = self.esd.event_rows["core.commit"]

        # Spot-check that a few fields have been properly posted/pulled from
        # the transaction
        inst_a = list(inst_commit[2700])[0]
        self.assertEqual(inst_a.data["program_counter"], 262184)

        inst_b = list(inst_commit[8100])[1]
        self.assertEqual(inst_b.data["program_counter"], 262212)

    def test_occupancy(self):
        feq = self.esd.event_rows["instruction_queue"]
        self.assertEqual(feq.event_count, 96)

        prev = 0
        non_zero = 0
        for events in feq.events.values():
            for event in events:
                count = int(event.abbrev)
                self.assertTrue(count == 0 or (count > 0 and abs(count - prev) == 1))
                if count > 1:
                    non_zero += 1
                prev = count

        self.assertGreater(non_zero, 0)

    def test_infer_period(self):
        self.assertEqual(self.esd.period, self.PS_PER_CYCLE)
