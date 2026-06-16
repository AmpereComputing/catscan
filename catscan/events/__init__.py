"""How to name/match events."""

import re
from collections.abc import Callable
from functools import cached_property


def rename_spec(arg: str) -> Callable[[str], str]:
    match, replacement = arg.split("=")

    match = regex_replace(match)
    match_re = re.compile("^" + match + "$")

    def rename(name: str) -> str:
        return match_re.sub(replacement, name)

    return rename


def matching_spec(arg: str) -> Callable[[str], str | None]:
    event = regex_replace(arg)
    event_re = re.compile(event)

    def match_event(e: str) -> str | None:
        return e if event_re.match(e) else None

    return match_event


def regex_replace(arg: str) -> str:
    return arg.replace(".", "\\.").replace("*", "[A-Za-z0-9_.]*").replace("?", "[A-Za-z0-9_]+").replace("#", "[0-9]+")


def parse_event(event_spec: str) -> tuple[str | Callable[[str], bool], list[str]]:
    event, *factors = event_spec.split("/")
    is_re = re.search("[*?#]", event)

    if is_re:
        event = matching_spec(event)

    return event, factors


class EventSpecification:
    _next_specification_index = 0

    def __init__(self, event_spec: str):
        self.event, self.factors = parse_event(event_spec)

        if callable(self.event):
            self.match_fn = self.event
        else:
            self.match_fn = self.string_name_match

        self.index = EventSpecification._next_specification_index
        EventSpecification._next_specification_index += 1

    def __call__(self, name: str) -> bool | str | None:
        return self.match_fn(name)

    def string_name_match(self, name: str) -> bool:
        return self.event == name

    @cached_property
    def long_factors(self) -> list[str]:
        event_base = ".".join(self.event.split(".")[:-1])
        factors = []
        for factor in self.factors:
            long_factor = factor.removeprefix(".") if "." in factor else f"{event_base}.{factor}"
            factors.append(long_factor)

        return factors
