# Copyright (c) 2024 Ampere Computing. All rights reserved.
# SPDX-License-Identifier: BSD-3-Clause

import textwrap
from collections.abc import Callable, Iterable
from typing import Any

import urwid

from catscan.data import EventStreamData
from catscan.state import CatscanState
from catscan.util import even_odd, hex_args_to_re
from catscan.widgets.button import ReleaseButton, ReleaseCheckBox, ReleaseUnpaddedButton


class EventDetailTransaction(urwid.WidgetWrap):
    """Row for associated transaction (and actions) with selected event."""

    def __init__(
        self,
        state: CatscanState,
        txids: int | Iterable[int],
        relationship: str,
        row_idx: int,
        on_highlight_transactions: Callable,
        on_unhighlight_transactions: Callable,
        on_go_to_transactions: Callable,
    ) -> None:
        self.state = state
        if isinstance(txids, int):
            self.txids = {txids}
        else:
            self.txids = set(txids)
        self.relationship = relationship
        self.row_idx = row_idx
        self.on_highlight_transactions = on_highlight_transactions
        self.on_unhighlight_transactions = on_unhighlight_transactions
        self.on_go_to_transactions = on_go_to_transactions

        (highlight_checkbox, goto_button) = self.rebuild_widgets()
        self.columns = urwid.Columns([("weight", 4, highlight_checkbox), ("weight", 1, goto_button)])

        super().__init__(urwid.AttrMap(self.columns, f"button_{even_odd(self.row_idx)}"))

    def rebuild_widgets(self) -> None:
        attr = f"button_{even_odd(self.row_idx)}"

        if len(self.txids) > 1:
            has_mixed = len(self.txids) > 1
            txn_text = f"{self.relationship}"
        else:
            has_mixed = False
            txid = self.txids.copy().pop()
            txn_text = f"txid {txid} ({self.relationship})"

        highlighted_txids = self.txids.intersection(set(self.state.highlighted_transactions.keys()))
        if highlighted_txids:
            state = True if len(highlighted_txids) == len(self.txids) else "mixed"
            color_list = [self.state.highlighted_transactions[t] for t in highlighted_txids]
            color_idx = max(color_list, key=color_list.count)
            attr = f"event_color_reversed_{color_idx}_{even_odd(self.row_idx)}"
        else:
            state = False

        state_change = self.unhighlight_transactions if state is True else self.highlight_transactions
        highlight_checkbox = ReleaseCheckBox(
            label=f"Mark {txn_text}", state=state, has_mixed=has_mixed, on_state_change=state_change
        )
        goto_button = urwid.Padding(
            ReleaseButton("Zoom", on_press=self.go_to_transactions, align="center"), align="center", width=8
        )

        return [urwid.AttrMap(w, attr) for w in [highlight_checkbox, goto_button]]

    def update_state(self, new_state: CatscanState) -> None:
        if new_state == self.state:
            return

        self.state = new_state
        (highlight_checkbox, goto_button) = self.rebuild_widgets()
        self.columns.contents = [
            (highlight_checkbox, (urwid.WHSettings.WEIGHT, 4, False)),
            (goto_button, (urwid.WHSettings.WEIGHT, 1, False)),
        ]

    def highlight_transactions(self, _widget, _user_args=None) -> bool:
        return self.on_highlight_transactions(self.txids)

    def unhighlight_transactions(self, _widget, _user_args=None) -> bool:
        return self.on_unhighlight_transactions(self.txids)

    def go_to_transactions(self, _widget, _user_args=None) -> bool:
        return self.on_go_to_transactions(self.txids)


class EventDetailDataText(urwid.widget.Widget):
    """Event data value row text (key: value)."""

    _sizing = frozenset(["flow"])
    _selectable = True

    def __init__(
        self,
        key: str,
        value: str,
        row_idx: int,
        split: int,
    ) -> None:
        self.key = key  # data value header
        self.value = value  # data value
        self.row_idx = row_idx  # row number in EventDetail
        self.split = split  # the column index of the split between header and
        # value
        self.line_wrapper = textwrap.TextWrapper(expand_tabs=False, break_on_hyphens=False)

    def rows(self, size: tuple[int], focus: bool = False) -> int:
        (maxcol,) = size
        self.line_wrapper.width = maxcol
        assert self.split <= maxcol

        if len(self.key) <= self.split and maxcol - 2 - self.split >= len(self.value):
            return 1
        if len(self.key) + 1 <= maxcol and len(self.value) <= maxcol:
            return 2
        return len(self.line_wrapper.wrap(self.key + ":")) + len(self.line_wrapper.wrap(self.value))

    def render(
        self,
        size: tuple[()] | tuple[int] | tuple[int, int],
        focus: bool = False,
    ) -> urwid.canvas.Canvas:
        (maxcol,) = size
        self.line_wrapper.width = maxcol

        attr = f"{even_odd(self.row_idx)}_event_row"
        if focus:
            attr = "focused_event_row"

        def pad_right(string: str) -> str:
            return string + " " * (maxcol - len(string))

        def pad_left(string: str) -> str:
            return " " * (maxcol - len(string)) + string

        lines = []

        name_len = len(self.key)
        value_len = len(self.value)
        max_value_len = maxcol - 2 - self.split
        if name_len <= self.split and max_value_len >= value_len:
            lines.append(pad_right(f"{self.key:<{self.split}}: {self.value:<{max_value_len}}"))
        else:
            header = f"{self.key:<{self.split}}:"
            if len(header) <= maxcol:
                lines.append(pad_right(header))
            else:
                lines += self.line_wrapper.wrap(header)
            if value_len <= max_value_len:
                lines.append(pad_left(f"{self.value:<{max_value_len}}"))
            elif value_len <= maxcol:
                lines.append(pad_left(f"{self.value:>{maxcol}}"))
            else:
                lines += self.line_wrapper.wrap(self.value)

        byte_strings = [line.encode() for line in lines]
        attrs = [[(attr, len(byte_string))] for byte_string in byte_strings]

        return urwid.canvas.TextCanvas(byte_strings, attrs)


class EventDetailData(urwid.WidgetWrap):
    """Event data value row, with text and additional action button(s)."""

    def __init__(
        self,
        name: str,
        key: str,
        value: str,
        row_idx: int,
        split: int,
        on_search: Callable,
    ) -> None:
        def _search_field(_widget, _user_args=None):
            on_search(value, search_fields=[name])

        self.key = key
        self.on_search = on_search
        self.text = EventDetailDataText(key, value, row_idx, split)
        self.search_button = ReleaseUnpaddedButton("🔎︎", on_press=_search_field)
        self.columns = urwid.Columns([self.text, ("pack", self.search_button)])

        super().__init__(urwid.AttrMap(self.columns, f"button_{even_odd(row_idx)}"))

    @property
    def split(self) -> int:
        return self.text.split

    @split.setter
    def split(self, value: int):
        self.text.split = value


class EventDetail(urwid.WidgetWrap):
    MAX_TXID_ROWS_PER_TYPE = 20

    """
    This widget display the selected event's data items/metadata and allows for
    selecting or zooming to its' related transactions.
    """

    def __init__(
        self,
        state: CatscanState,
        stream_data: EventStreamData,
        hex_args: Iterable[str],
        on_clear_selection: Callable,
        on_highlight_transactions: Callable,
        on_unhighlight_transactions: Callable,
        on_go_to_transactions: Callable,
        on_search: Callable,
    ) -> None:
        self.state = state
        self.stream_data = stream_data
        self.hexargs_re = hex_args_to_re(hex_args)
        self.on_clear_selection = on_clear_selection
        self.on_highlight_transactions = on_highlight_transactions
        self.on_unhighlight_transactions = on_unhighlight_transactions
        self.on_go_to_transactions = on_go_to_transactions
        self.on_search = on_search

        self.detail_data_widgets = []
        self.detail_transaction_data_widgets = []
        self.detail_transaction_widgets = []
        self._rows = self.recreate_rows()
        self.list_walker = urwid.SimpleFocusListWalker(self._rows)
        self.list_box = urwid.ListBox(self.list_walker)
        self.scrollable = urwid.ScrollBar(self.list_box, trough_char="│")

        self._event_data = None
        self._event_transaction_data = None

        super().__init__(self.scrollable)

    def map_hex_args(self, display_data: dict[str, Any]) -> dict[str, Any]:
        def format_as_hex(item: Any) -> Any:
            name, value = item
            if self.hexargs_re.match(name):
                if isinstance(value, str):
                    return name, hex(int(value, base=0))
                return name, hex(value)

            return item

        return dict(map(format_as_hex, display_data.items()))

    def _event_data_dictionary(self) -> dict[str, tuple[str, str | None]]:
        """
        Return a dictionary of the selected event's data items, with truncated
        prefixes.
        """
        event = self.state.selection.event

        def strip_prefix(to_strip: str, to_compare: str) -> str:
            # Strip a common prefix from to_strip if it shares a prefix with
            # to_compare
            def prefix(s: str) -> str:
                return ".".join(s.split(".")[:-1])

            if prefix(to_strip) == prefix(to_compare):
                # remove duplicate prefixes (i.e. those that the data items
                # share with their parent event)
                return to_strip.split(".")[-1]
            return to_strip

        # Modify (a copy of) the event data items as the user has requested
        display_data = self.map_hex_args(event.data)
        if self.state.sort_event_keys:
            display_data = dict(sorted(display_data.items()))

        cycles = round(event.time // self.state.ps_per_cycle)
        data = [
            ("abbrev", (event.abbrev, None)),
            ("time", (f"{event.time:,} ps / {cycles:,} cyc", None)),
        ] + [(strip_prefix(name, event.name), (value, name)) for name, value in display_data.items()]

        return dict(data)

    @property
    def event_data(self) -> dict[str, tuple[str, str | None]]:
        if self._event_data is None:
            self._event_data = self._event_data_dictionary()
        return self._event_data

    def _event_transaction_data_dictionary(self) -> dict[str, Any]:
        """
        Return a dictionary of the selected event's transaction data items.
        """
        event = self.state.selection.event
        if "txid" in event.data:
            txid = event.data["txid"]
            if txid in self.stream_data.transactions:
                data = self.map_hex_args(self.stream_data.transactions[txid].data)
                if self.state.sort_event_keys:
                    data = dict(sorted(data.items()))

                return data

        return {}

    @property
    def event_transaction_data(self) -> dict[str, Any]:
        if self._event_transaction_data is None:
            self._event_transaction_data = self._event_transaction_data_dictionary()
        return self._event_transaction_data

    def get_header_width(self, maxcol: int, data: dict) -> int:
        maxcol -= 2  # allow for ": " separating header and value
        if maxcol <= 0:
            return 0

        header_lengths = sorted([len(h) for h in data])
        value_lengths = sorted([len(str(v[0])) if isinstance(v, tuple) else len(str(v)) for v in data.values()])

        best_split = None
        best_split_num_unsplit = None

        for split in range(maxcol + 1):
            current_unsplit = sum([1 if l <= split else 0 for l in header_lengths]) + sum(
                [1 if l < maxcol - split else 0 for l in value_lengths]
            )
            if best_split is None or current_unsplit > best_split_num_unsplit:
                best_split = split
                best_split_num_unsplit = current_unsplit
        return best_split

    def recreate_rows(self) -> list[urwid.Widget]:
        self.detail_data_widgets = []

        if not self.state.selection.is_event():
            return [urwid.Text("")]

        event = self.state.selection.event
        data = self.event_data

        rows = [urwid.AttrMap(urwid.Text(event.name, align="center"), "focused_event_row")]
        rows.append(urwid.Text(""))
        row_idx = 0

        for name, (value, original_name) in data.items():
            if original_name is None:
                self.detail_data_widgets.append(EventDetailDataText(name, str(value), row_idx, 0))
            else:
                self.detail_data_widgets.append(
                    EventDetailData(original_name, name, str(value), row_idx, 0, self.on_search)
                )
            row_idx += 1
        rows += self.detail_data_widgets

        self.detail_transaction_widgets = []
        self.detail_transaction_data_widgets = []

        if "txid" in event.data:
            txid = event.data["txid"]
            transaction = self.stream_data.transactions[txid]

            if self.event_transaction_data:
                rows.append(urwid.Text(""))
                rows.append(urwid.AttrMap(urwid.Text("Transaction Data", align="center"), "focused_event_row"))

                for name, value in self.event_transaction_data.items():
                    self.detail_transaction_data_widgets.append(EventDetailDataText(name, str(value), row_idx, 0))
                    row_idx += 1

                rows += self.detail_transaction_data_widgets

            rows.append(urwid.Text(""))
            rows.append(urwid.AttrMap(urwid.Text("Transactions", align="center"), "focused_event_row"))

            parents = list(transaction.parents)[: EventDetail.MAX_TXID_ROWS_PER_TYPE]
            children = list(transaction.children)[: EventDetail.MAX_TXID_ROWS_PER_TYPE]
            ancestors = [a.txid for a in self.stream_data.ancestors(txid)][: EventDetail.MAX_TXID_ROWS_PER_TYPE] + [
                txid
            ]
            descendants = [a.txid for a in self.stream_data.descendants(txid)][: EventDetail.MAX_TXID_ROWS_PER_TYPE] + [
                txid
            ]

            if len(ancestors) > 1:
                self.detail_transaction_widgets.append(
                    EventDetailTransaction(
                        self.state,
                        ancestors,
                        "self + ancestors",
                        row_idx,
                        on_highlight_transactions=self.on_highlight_transactions,
                        on_unhighlight_transactions=self.on_unhighlight_transactions,
                        on_go_to_transactions=self.on_go_to_transactions,
                    )
                )
                row_idx += 1
            if len(descendants) > 1:
                self.detail_transaction_widgets.append(
                    EventDetailTransaction(
                        self.state,
                        descendants,
                        "self + descendants",
                        row_idx,
                        on_highlight_transactions=self.on_highlight_transactions,
                        on_unhighlight_transactions=self.on_unhighlight_transactions,
                        on_go_to_transactions=self.on_go_to_transactions,
                    )
                )
                row_idx += 1

            self.detail_transaction_widgets.append(
                EventDetailTransaction(
                    self.state,
                    txid,
                    "self",
                    row_idx,
                    on_highlight_transactions=self.on_highlight_transactions,
                    on_unhighlight_transactions=self.on_unhighlight_transactions,
                    on_go_to_transactions=self.on_go_to_transactions,
                )
            )
            row_idx += 1

            for parent_txid in parents:
                self.detail_transaction_widgets.append(
                    EventDetailTransaction(
                        self.state,
                        parent_txid,
                        "parent",
                        row_idx,
                        on_highlight_transactions=self.on_highlight_transactions,
                        on_unhighlight_transactions=self.on_unhighlight_transactions,
                        on_go_to_transactions=self.on_go_to_transactions,
                    )
                )
                row_idx += 1
            for child_txid in children:
                self.detail_transaction_widgets.append(
                    EventDetailTransaction(
                        self.state,
                        child_txid,
                        "child",
                        row_idx,
                        on_highlight_transactions=self.on_highlight_transactions,
                        on_unhighlight_transactions=self.on_unhighlight_transactions,
                        on_go_to_transactions=self.on_go_to_transactions,
                    )
                )
                row_idx += 1
            rows += self.detail_transaction_widgets

        rows.append(urwid.Text(""))

        rows.append(
            urwid.Padding(
                urwid.AttrMap(ReleaseButton("Close", align="center", on_press=self.clear_selection), "button"),
                align="center",
                width=9,
            )
        )

        return rows

    def update_state(self, new_state: CatscanState) -> None:
        if new_state == self.state:
            return

        new_selection = self.state.selection != new_state.selection
        self.state = new_state
        if new_selection:
            self._event_data = None
            self._event_transaction_data = None
            self.list_walker.clear()
            self._rows = self.recreate_rows()
            self.list_walker += self._rows
        else:
            for tx_widget in self.detail_transaction_widgets:
                tx_widget.update_state(new_state)

    def update_stream_data(self, stream_data: EventStreamData) -> None:
        self.stream_data = stream_data
        self.list_walker.clear()
        self._rows = self.recreate_rows()
        self.list_walker += self._rows
        self._invalidate()

    def clear_selection(self, _widget) -> bool:
        return self.on_clear_selection()

    def needs_scrollbar(self, size: (int, int)) -> bool:
        (maxcol, maxrow) = size
        split = self.get_header_width(maxcol, self.event_data)
        for w in self.detail_data_widgets:
            w.split = split

        split = self.get_header_width(maxcol, self.event_transaction_data)
        for w in self.detail_transaction_data_widgets:
            w.split = split

        row_size = 0
        for row in self._rows:
            row_size += row.rows((maxcol,))
            if row_size > maxrow:
                return True
        return False

    def render(
        self,
        size: tuple[()] | tuple[int] | tuple[int, int],
        focus: bool = False,
    ) -> urwid.canvas.Canvas:
        (maxcol, _maxrow) = size
        # Update the header/value split
        split = self.get_header_width(maxcol, self.event_data)
        for w in self.detail_data_widgets:
            w.split = split

        split = self.get_header_width(maxcol, self.event_transaction_data)
        for w in self.detail_transaction_data_widgets:
            w.split = split

        return self._w.render(size, focus)
