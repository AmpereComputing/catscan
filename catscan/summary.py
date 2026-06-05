# Copyright (c) 2024 Ampere Computing. All rights reserved.
# SPDX-License-Identifier: BSD-3-Clause

from collections import Counter
from collections.abc import Sequence

from catscan.data import EventStreamDataView, summary_histogram


def generate_summary_table(
    stream_data: EventStreamDataView,
    start_ps: int,
    end_ps: int,
    rows: Sequence[str | int],
    fields: Sequence[str],
) -> tuple[list[str], list[list[str]], list[str]]:
    """
    Given a region of an event stream, generate a table of a of the event
    'abbreviations'. Return the table as lists containing the strings making up
    the header, body (contents), and footer.
    """
    # First, generate the histogram of all the events' abbreviations in the
    # given rows and time window
    fields = fields or [None]
    histogram = Counter()
    for row in rows:
        for field in fields:
            histogram += summary_histogram(stream_data.get(row)[start_ps:end_ps], field=field)

    # Then, find the most frequent abbreviations and display them
    max_rows = 99
    frequent_abbrevs = histogram.most_common(max_rows)

    total = histogram.total()

    header = ["abbreviation", "frequency (% of total)"]
    contents = []
    for rank, (abbrev, count) in enumerate(frequent_abbrevs):
        rank_str = f"{rank + 1}."
        header_message = f"{rank_str:3} {abbrev}"
        count_message = f"{count} ({count / total:.2%})"
        contents.append([header_message, count_message])

    if not contents:
        contents.append(["[no events]", "- (-%)"])

    not_shown = total - sum([count for _, count in frequent_abbrevs])
    if not_shown:
        others_header = f"all others ({len(frequent_abbrevs) + 1}-{len(histogram)})"
        others_count = f"{not_shown} ({not_shown / total:.2%})"

        contents.append(["⋮", "⋮"])
        contents.append([others_header, others_count])

    footer = ["total", f"{total} (100%)"] if len(frequent_abbrevs) > 1 else []

    return header, contents, footer
