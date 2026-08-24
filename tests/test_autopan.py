# Copyright (c) 2026 Ampere Computing. All rights reserved.
# SPDX-License-Identifier: BSD-3-Clause

from fractions import Fraction

from catscan.autopan import AutopanMode
from catscan.commit_sync import CommitSyncState
from catscan.data import DataView
from tests import DummyCommitSyncer, TransactionSyncDataTest


class AutopanTest(TransactionSyncDataTest):
    def _set_start_time(self, top, start_ps):
        top.update_state(top.state.copy_with(start_ps=start_ps, cycles_per_char=Fraction(1, 1)))


class TestNoAutopan(AutopanTest):
    def setUp(self):
        super().setUp()
        self.top = self.make_top(size=(20, 1))
        self._set_start_time(self.top, 200)

    def test_none_does_not_pan_on_vertical_navigation(self):
        self.top._transaction_view.keypress((20, 1), "down")

        self.assertEqual(self.top.state.start_ps, 200)


class TestFirstEventAutopan(AutopanTest):
    def setUp(self):
        super().setUp()
        self.top = self.make_top(size=(20, 1))
        self.top.autopan.mode = AutopanMode.FIRST_EVENT
        self.view = self.top._transaction_view
        self.view_width = self.top.state.column_header_width + 3

    def test_first_event_pans_transaction_row_to_its_first_event(self):
        self._set_start_time(self.top, 200)

        self.view.keypress((20, 1), "down")

        self.assertEqual(self.top.state.start_ps, 40)

    def test_first_event_stays_visible_after_coarse_zoom_alignment(self):
        self.top.update_state(self.top.state.copy_with(cycles_per_char=Fraction(4, 1), start_ps=200))
        self.view.render((self.view_width, 1), focus=True)
        self.view.scroll_row_to_edge(self.txids[1], "top")

        self.view.keypress((self.view_width, 1), "down")

        start_ps, end_ps = self.view.visible_time_range()
        self.assertLessEqual(start_ps, 70)
        self.assertLess(70, end_ps)

    def test_first_event_pans_when_clicking_a_row(self):
        self._set_start_time(self.top, 200)
        self.view.render((20, 2), focus=True)

        self.view.mouse_event((20, 2), "mouse press", 1, self.top.state.column_header_width + 1, 1, True)

        self.assertEqual(self.view.focused_row_key(), self.txids[1])
        self.assertEqual(self.top.state.start_ps, 40)

    def test_visible_later_event_prevents_pan_for_sparse_row(self):
        self._set_start_time(self.top, 50)

        self.view.keypress((20, 1), "down")

        self.assertEqual(self.top.state.start_ps, 50)

    def test_fractional_zoom_uses_the_rendered_time_range(self):
        self.top.update_state(self.top.state.copy_with(cycles_per_char=Fraction(1, 2), start_ps=5))
        self.view.render((self.view_width, 1), focus=True)
        self.view.scroll_row_to_edge(self.txids[1], "top")

        self.assertEqual(self.view.visible_time_range(), (0, 20))
        self.view.keypress((self.view_width, 1), "up")

        self.assertEqual(self.top.state.start_ps, 5)


class TestNearestEventAutopan(AutopanTest):
    def setUp(self):
        super().setUp()
        self.top = self.make_top(size=(20, 1))
        self.top.autopan.mode = AutopanMode.NEAREST_EVENT
        self.view = self.top._transaction_view
        self.view_width = self.top.state.column_header_width + 3

    def test_nearest_event_pans_right_row_to_its_first_event(self):
        self._set_start_time(self.top, 0)
        self.view.render((self.view_width, 1), focus=True)

        self.view.keypress((self.view_width, 1), "down")

        self.assertEqual(self.top.state.start_ps, 40)

    def test_nearest_event_pans_left_row_to_its_last_event(self):
        self._set_start_time(self.top, 100)
        self.view.render((self.view_width, 1), focus=True)
        self.view.scroll_row_to_edge(self.txids[1], "top")

        self.view.keypress((self.view_width, 1), "down")

        self.assertEqual(self.top.state.start_ps, 60)
        self.assertEqual(
            self.view.visible_time_range()[1] - self.top.state.ps_per_cycle,
            self.view.data_view().get(self.txids[2]).last_time,
        )

    def test_nearest_event_pans_to_the_closest_later_sparse_event(self):
        self.view.render((self.view_width, 1), focus=True)
        self.view.visible_time_range = lambda: (11, 19)

        target_time = self.top.autopan.target_time(
            self.view,
            self.view.data_view().get(self.txids[0]),
            commit_syncer=self.top.commit_syncer,
        )

        self.assertEqual(target_time, 20)

    def test_nearest_event_pans_to_the_closest_earlier_sparse_event(self):
        self.view.render((self.view_width, 1), focus=True)
        self.view.visible_time_range = lambda: (11, 18)

        target_time = self.top.autopan.target_time(
            self.view,
            self.view.data_view().get(self.txids[0]),
            commit_syncer=self.top.commit_syncer,
        )

        self.assertEqual(target_time, self.top.autopan._last_event_start_time(self.view, 10))


class TestCommitEventAutopan(AutopanTest):
    def setUp(self):
        super().setUp()
        self.top = self.make_top(size=(20, 1))
        self.top.autopan.mode = AutopanMode.COMMIT_EVENT
        self.view = self.top._transaction_view

    def test_commit_event_pans_to_shared_commit_anchor(self):
        self.top.commit_syncer = DummyCommitSyncer(other_commit_index={20: 50})
        self._set_start_time(self.top, 200)

        self.view.keypress((20, 1), "down")

        self.assertEqual(self.top.state.start_ps, 50)

    def test_commit_event_without_sync_falls_back_to_first_event(self):
        self._set_start_time(self.top, 200)

        self.view.keypress((20, 1), "down")

        self.assertEqual(self.top.state.start_ps, 40)

    def test_commit_event_uses_zero_timestamp_anchor(self):
        self.top.autopan._commit_time = lambda *_args: 0
        self._set_start_time(self.top, 200)

        target_time = self.top.autopan.target_time(
            self.view,
            self.view.data_view().get(self.txids[1]),
            commit_syncer=self.top.commit_syncer,
        )

        self.assertEqual(target_time, 0)

    def test_commit_event_falls_back_to_first_event_without_anchor(self):
        self.view.scroll_row_to_edge(self.txids[1], "top")
        self._set_start_time(self.top, 200)

        self.view.keypress((20, 1), "down")

        self.assertEqual(self.top.state.start_ps, 70)

    def test_commit_event_falls_back_when_no_commit_is_shared(self):
        self.top.commit_syncer = DummyCommitSyncer(other_commit_index={999: 999})
        self._set_start_time(self.top, 200)

        self.view.keypress((20, 1), "down")

        self.assertEqual(self.top.state.start_ps, 40)


class TestAutopanEventTime(AutopanTest):
    def setUp(self):
        super().setUp()
        self.top = self.make_top(size=(20, 1))

    def test_event_time_uses_the_requested_event_name(self):
        row = self.top._transaction_view.data_view().get(self.txids[1])

        self.assertEqual(self.top.autopan._event_time(row, "core.commit"), 50)
        self.assertEqual(self.top.autopan._event_time(row, "work"), 40)


class TestFirstEventResourceAutopan(AutopanTest):
    def setUp(self):
        super().setUp()
        self.top = self.make_top(size=(20, 1), view=DataView.RESOURCE)
        self.top.autopan.mode = AutopanMode.FIRST_EVENT
        self.view = self.top._resource_view

    def test_first_event_pans_resource_row(self):
        target_row = self.view.iter_event_rows().keys()[0]
        self._set_start_time(self.top, 200)

        self.view.keypress((20, 1), "down")

        self.assertEqual(self.top.state.start_ps, self.view.data_view().get(target_row).first_time)

    def test_resource_group_navigation_does_not_attempt_to_auto_pan(self):
        self.view.render((20, 1), focus=True)
        self.view.scroll_row_to_edge(self.view.iter_event_rows().keys()[0], "top")
        self._set_start_time(self.top, 200)

        self.view.keypress((20, 1), "g-g")

        self.assertIsNone(self.view.focused_row_key())
        self.assertEqual(self.top.state.start_ps, 200)


class TestAutopanCommands(AutopanTest):
    def setUp(self):
        super().setUp()
        self.top = self.make_top(size=(20, 1))
        self.top.update_state(self.top.state.copy_with(loading=False))

    def test_command_changes_mode_and_rejects_resource_commit_mode(self):
        self.assertTrue(self.top.command("autopan first-event"))
        self.assertEqual(self.top.autopan.mode, AutopanMode.FIRST_EVENT)
        self.assertFalse(self.top.command("autopan"))
        self.assertIn("mode expected", self.top.state.messages[-1])
        self.assertFalse(self.top.command("autopan invalid"))
        self.assertEqual(self.top.autopan.mode, AutopanMode.FIRST_EVENT)
        self.assertIn("not a valid AutopanMode", self.top.state.messages[-1])
        self.assertFalse(self.top.command("help autopan"))
        self.assertTrue(self.top.state.show_help)
        self.assertEqual(self.top.help._command_filter, "autopan")

        resource_top = self.make_top(size=(20, 1), view=DataView.RESOURCE)
        resource_top.update_state(resource_top.state.copy_with(loading=False))
        self.assertFalse(resource_top.command("autopan commit-event"))
        self.assertEqual(resource_top.autopan.mode, AutopanMode.NONE)
        self.assertIn("requires the transactions view", resource_top.state.messages[-1])


class TestInboundCommitEventAutopan(AutopanTest):
    def setUp(self):
        super().setUp()
        self.top = self.make_top(size=(20, 2))
        self.top.autopan.mode = AutopanMode.COMMIT_EVENT
        self.sync_state = CommitSyncState(
            sync_index=40,
            cycles_per_char=Fraction(1, 1),
            expand_rows=False,
            chars_rel_to_start=0,
        )

    def _configure_transaction_sync(self):
        self.top.commit_syncer = DummyCommitSyncer(other_view_mode=DataView.TRANSACTIONS)

    def test_inbound_transaction_sync_uses_received_commit_anchor_without_echo(self):
        self._set_start_time(self.top, 200)
        self._configure_transaction_sync()

        self.top.receive_commit_sync(self.sync_state)

        self.assertEqual(self.top.state.start_ps, 100)
        self.assertEqual(self.top.commit_syncer.sent, [])

    def test_inbound_visible_transaction_row_does_not_pan_or_echo(self):
        self._set_start_time(self.top, 100)
        self._configure_transaction_sync()

        self.top.receive_commit_sync(self.sync_state)

        self.assertEqual(self.top.state.start_ps, 100)
        self.assertEqual(self.top.commit_syncer.sent, [])
