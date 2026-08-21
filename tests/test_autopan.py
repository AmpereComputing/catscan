# Copyright (c) 2026 Ampere Computing. All rights reserved.
# SPDX-License-Identifier: BSD-3-Clause

from fractions import Fraction

from catscan.commands import AutopanMode
from catscan.commit_sync import CommitSyncState
from catscan.data import DataView
from tests import DummyCommitSyncer, TransactionSyncDataTest


class TestAutopan(TransactionSyncDataTest):
    def _set_start_time(self, top, start_ps):
        top.update_state(top.state.copy_with(start_ps=start_ps, cycles_per_char=Fraction(1, 1)))

    def test_none_does_not_pan_on_vertical_navigation(self):
        top = self.make_top(size=(20, 1))
        self._set_start_time(top, 200)

        top._transaction_view.keypress((20, 1), "down")

        self.assertEqual(top.state.start_ps, 200)

    def test_first_event_pans_transaction_row_to_its_first_event(self):
        top = self.make_top(size=(20, 1))
        top.autopan.mode = AutopanMode.FIRST_EVENT
        self._set_start_time(top, 200)

        top._transaction_view.keypress((20, 1), "down")

        self.assertEqual(top.state.start_ps, 40)

    def test_first_event_stays_visible_after_coarse_zoom_alignment(self):
        top = self.make_top(size=(20, 1))
        top.autopan.mode = AutopanMode.FIRST_EVENT
        view = top._transaction_view
        view_width = top.state.column_header_width + 3
        top.update_state(top.state.copy_with(cycles_per_char=Fraction(4, 1), start_ps=200))
        view.render((view_width, 1), focus=True)
        view.scroll_row_to_edge(self.txids[1], "top")

        view.keypress((view_width, 1), "down")

        start_ps, end_ps = view.visible_time_range()
        self.assertLessEqual(start_ps, 70)
        self.assertLess(70, end_ps)

    def test_first_event_pans_when_clicking_a_row(self):
        top = self.make_top(size=(20, 2))
        top.autopan.mode = AutopanMode.FIRST_EVENT
        view = top._transaction_view
        self._set_start_time(top, 200)
        view.render((20, 2), focus=True)

        view.mouse_event((20, 2), "mouse press", 1, top.state.column_header_width + 1, 1, True)

        self.assertEqual(view.focused_row_key(), self.txids[1])
        self.assertEqual(top.state.start_ps, 40)

    def test_nearest_event_pans_right_row_to_its_first_event(self):
        top = self.make_top(size=(20, 1))
        top.autopan.mode = AutopanMode.NEAREST_EVENT
        view = top._transaction_view
        view_width = top.state.column_header_width + 3
        self._set_start_time(top, 0)
        view.render((view_width, 1), focus=True)

        view.keypress((view_width, 1), "down")

        self.assertEqual(top.state.start_ps, 40)

    def test_nearest_event_pans_left_row_to_its_last_event(self):
        top = self.make_top(size=(20, 1))
        top.autopan.mode = AutopanMode.NEAREST_EVENT
        view = top._transaction_view
        view_width = top.state.column_header_width + 3
        self._set_start_time(top, 100)
        view.render((view_width, 1), focus=True)
        view.scroll_row_to_edge(self.txids[1], "top")

        view.keypress((view_width, 1), "down")

        self.assertEqual(top.state.start_ps, 60)
        self.assertEqual(
            view.visible_time_range()[1] - top.state.ps_per_cycle,
            view.data_view().get(self.txids[2]).last_time,
        )

    def test_nearest_event_pans_to_the_closest_later_sparse_event(self):
        top = self.make_top(size=(20, 1))
        top.autopan.mode = AutopanMode.NEAREST_EVENT
        view = top._transaction_view
        view.render((top.state.column_header_width + 3, 1), focus=True)
        view.visible_time_range = lambda: (11, 19)

        target_time = top.autopan.target_time(
            view,
            view.data_view().get(self.txids[0]),
            commit_syncer=top.commit_syncer,
        )

        self.assertEqual(target_time, 20)

    def test_nearest_event_pans_to_the_closest_earlier_sparse_event(self):
        top = self.make_top(size=(20, 1))
        top.autopan.mode = AutopanMode.NEAREST_EVENT
        view = top._transaction_view
        view.render((top.state.column_header_width + 3, 1), focus=True)
        view.visible_time_range = lambda: (11, 18)

        target_time = top.autopan.target_time(
            view,
            view.data_view().get(self.txids[0]),
            commit_syncer=top.commit_syncer,
        )

        self.assertEqual(target_time, top.autopan._last_event_start_time(view, 10))

    def test_visible_later_event_prevents_pan_for_sparse_row(self):
        top = self.make_top(size=(20, 1))
        top.autopan.mode = AutopanMode.FIRST_EVENT
        self._set_start_time(top, 50)

        top._transaction_view.keypress((20, 1), "down")

        self.assertEqual(top.state.start_ps, 50)

    def test_fractional_zoom_uses_the_rendered_time_range(self):
        top = self.make_top(size=(20, 1))
        top.autopan.mode = AutopanMode.FIRST_EVENT
        view = top._transaction_view
        view_width = top.state.column_header_width + 3
        top.update_state(top.state.copy_with(cycles_per_char=Fraction(1, 2), start_ps=5))
        view.render((view_width, 1), focus=True)
        view.scroll_row_to_edge(self.txids[1], "top")

        self.assertEqual(view.visible_time_range(), (0, 20))
        view.keypress((view_width, 1), "up")

        self.assertEqual(top.state.start_ps, 5)

    def test_commit_event_pans_to_shared_commit_anchor(self):
        top = self.make_top(size=(20, 1))
        top.autopan.mode = AutopanMode.COMMIT_EVENT
        top.commit_syncer = DummyCommitSyncer(other_commit_index={20: 50})
        self._set_start_time(top, 200)

        top._transaction_view.keypress((20, 1), "down")

        self.assertEqual(top.state.start_ps, 50)

    def test_commit_event_without_sync_falls_back_to_first_event(self):
        top = self.make_top(size=(20, 1))
        top.autopan.mode = AutopanMode.COMMIT_EVENT
        self._set_start_time(top, 200)

        top._transaction_view.keypress((20, 1), "down")

        self.assertEqual(top.state.start_ps, 40)

    def test_commit_event_uses_zero_timestamp_anchor(self):
        top = self.make_top(size=(20, 1))
        top.autopan.mode = AutopanMode.COMMIT_EVENT
        top.autopan._commit_time = lambda *_args: 0
        self._set_start_time(top, 200)

        target_time = top.autopan.target_time(
            top._transaction_view,
            top._transaction_view.data_view().get(self.txids[1]),
            commit_syncer=top.commit_syncer,
        )

        self.assertEqual(target_time, 0)

    def test_commit_event_falls_back_to_first_event_without_anchor(self):
        top = self.make_top(size=(20, 1))
        top.autopan.mode = AutopanMode.COMMIT_EVENT
        top._transaction_view.scroll_row_to_edge(self.txids[1], "top")
        self._set_start_time(top, 200)

        top._transaction_view.keypress((20, 1), "down")

        self.assertEqual(top.state.start_ps, 70)

    def test_commit_event_falls_back_when_no_commit_is_shared(self):
        top = self.make_top(size=(20, 1))
        top.autopan.mode = AutopanMode.COMMIT_EVENT
        top.commit_syncer = DummyCommitSyncer(other_commit_index={999: 999})
        self._set_start_time(top, 200)

        top._transaction_view.keypress((20, 1), "down")

        self.assertEqual(top.state.start_ps, 40)

    def test_first_event_pans_resource_row(self):
        top = self.make_top(size=(20, 1), view=DataView.RESOURCE)
        top.autopan.mode = AutopanMode.FIRST_EVENT
        view = top._resource_view
        target_row = view.iter_event_rows().keys()[0]
        self._set_start_time(top, 200)

        view.keypress((20, 1), "down")

        self.assertEqual(top.state.start_ps, view.data_view().get(target_row).first_time)

    def test_resource_group_navigation_does_not_attempt_to_auto_pan(self):
        top = self.make_top(size=(20, 1), view=DataView.RESOURCE)
        top.autopan.mode = AutopanMode.FIRST_EVENT
        view = top._resource_view
        view.render((20, 1), focus=True)
        view.scroll_row_to_edge(view.iter_event_rows().keys()[0], "top")

        view.keypress((20, 1), "g-g")

        self.assertIsNone(view.focused_row_key())

    def test_command_changes_mode_and_rejects_resource_commit_mode(self):
        top = self.make_top(size=(20, 1))
        top.update_state(top.state.copy_with(loading=False))

        self.assertTrue(top.command("autopan first-event"))
        self.assertEqual(top.autopan.mode, AutopanMode.FIRST_EVENT)
        self.assertFalse(top.command("autopan"))
        self.assertIn("mode expected", top.state.messages[-1])
        self.assertFalse(top.command("autopan invalid"))
        self.assertEqual(top.autopan.mode, AutopanMode.FIRST_EVENT)
        self.assertIn("not a valid AutopanMode", top.state.messages[-1])
        self.assertFalse(top.command("help autopan"))
        self.assertTrue(top.state.show_help)
        self.assertEqual(top.help._command_filter, "autopan")

        resource_top = self.make_top(size=(20, 1), view=DataView.RESOURCE)
        resource_top.update_state(resource_top.state.copy_with(loading=False))
        self.assertFalse(resource_top.command("autopan commit-event"))
        self.assertEqual(resource_top.autopan.mode, AutopanMode.NONE)
        self.assertIn("requires the transactions view", resource_top.state.messages[-1])

    def test_inbound_transaction_sync_uses_received_commit_anchor_without_echo(self):
        top = self.make_top(size=(20, 2))
        top.autopan.mode = AutopanMode.COMMIT_EVENT
        self._set_start_time(top, 200)
        top.commit_syncer = DummyCommitSyncer(other_view_mode=DataView.TRANSACTIONS)

        top.receive_commit_sync(
            CommitSyncState(
                sync_index=40,
                cycles_per_char=Fraction(1, 1),
                expand_rows=False,
                chars_rel_to_start=0,
            )
        )

        self.assertEqual(top.state.start_ps, 100)
        self.assertEqual(top.commit_syncer.sent, [])

    def test_inbound_visible_transaction_row_does_not_pan_or_echo(self):
        top = self.make_top(size=(20, 2))
        top.autopan.mode = AutopanMode.COMMIT_EVENT
        self._set_start_time(top, 100)
        top.commit_syncer = DummyCommitSyncer(other_view_mode=DataView.TRANSACTIONS)

        top.receive_commit_sync(
            CommitSyncState(
                sync_index=40,
                cycles_per_char=Fraction(1, 1),
                expand_rows=False,
                chars_rel_to_start=0,
            )
        )

        self.assertEqual(top.state.start_ps, 100)
        self.assertEqual(top.commit_syncer.sent, [])
