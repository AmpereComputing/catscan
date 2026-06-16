# Copyright (c) 2025 Ampere Computing. All rights reserved.
# SPDX-License-Identifier: BSD-3-Clause

from abc import ABC, abstractmethod
from collections.abc import Callable, Iterable
from typing import Protocol


class SupportsStringConversion(Protocol):
    def __str__(self) -> str: ...


class FilteredSuggestions(ABC):
    """Common completion suggestion interface."""

    def __init__(self, fuzzy: bool = False) -> None:
        self._current = -1
        self._original = None
        self._original_suffix = None
        self._fuzzy = fuzzy
        self.reset()

    @abstractmethod
    def suggestions(self) -> list[str]:
        """Suggestions for filtering."""

    def fallback_suggestions(self) -> list[str]:
        """Fallback suggestions if none of the suggestions match."""
        return []

    @property
    def _filtered_suggestions(self) -> list[str]:
        suggestions = self.suggestions()
        if self._original is None:
            return suggestions

        def matches(text: str) -> bool:
            return self._original in text if self._fuzzy else text.startswith(self._original)

        if matching := list(filter(matches, suggestions)):
            return matching

        return list(filter(matches, self.fallback_suggestions()))

    @property
    def total_current_suggestions(self) -> int:
        return len(self._filtered_suggestions)

    def _get(self) -> str | None:
        filtered = self._filtered_suggestions
        if not filtered or self._current < 0:
            return self._original
        if self._current >= len(filtered):
            return filtered[-1] if filtered else None
        return filtered[self._current]

    def _assign(self, text: str | None) -> None:
        self._original = text

    def back(self, current_text: str) -> str | None:
        """Back in suggestions."""
        if self._current < 0:
            self._assign(current_text)
        if self.total_current_suggestions > (self._current + 1):
            self._current += 1
        return self._get()

    def back_with_position(self, current_text: str, position: int) -> tuple[str | None, int]:
        """Back in suggestions with position.

        This (along with forward_with_position) should be used when supporting inline suggestions,
        which records the original trailing characters to adjust text.
        """
        if self._current < 0:
            self._original_suffix = current_text[position:]
        if self._original_suffix is None:
            raise ValueError("Suffix not set (intermixed '*_with_position' without)")

        value = self.back(current_text.removesuffix(self._original_suffix))
        if value is None:
            return value, 0
        return value + self._original_suffix, len(value)

    def forward(self) -> str | None:
        """Forward in suggestions."""
        if self._current >= 0:
            self._current -= 1
        return self._get()

    def forward_with_position(self) -> tuple[str | None, int]:
        """Forward in suggestions with position.

        This (along with back_with_position) should be used when supporting inline suggestions,
        which records the original trailing characters to adjust text.
        """
        if self._original_suffix is None:
            raise ValueError("Suffix not set (intermixed '*_with_position' without)")

        value = self.forward()
        if value is None:
            return value, 0
        return value + self._original_suffix, len(value)

    def update(self) -> None:
        self._current = min(self._current, self.total_current_suggestions - 1)

    def reset(self, current_text: str | None = None) -> str | None:
        self._current = -1
        self._original_suffix = None
        self._assign(current_text)
        return None


class HistoricalCompletion(FilteredSuggestions):
    """Complete based upon history."""

    MAX_HISTORY = 64

    def __init__(self) -> None:
        super().__init__()
        self._previous = []

    def suggestions(self) -> list[str]:
        return self._previous

    def add(self, command: str) -> None:
        self._previous.insert(0, command)
        if len(self._previous) > self.MAX_HISTORY:
            self._previous.pop()

        self.update()


class KeywordCompletion(FilteredSuggestions):
    def __init__(self, suggestions: Iterable[SupportsStringConversion], *, fuzzy: bool = False) -> None:
        super().__init__(fuzzy=fuzzy)
        self._suggestions = list(map(str, suggestions))
        self.update()

    def suggestions(self) -> list[str]:
        return self._suggestions


class CallableCompletion(FilteredSuggestions):
    def __init__(
        self,
        suggestion_callable: Callable[[], Iterable[SupportsStringConversion]],
        *,
        fuzzy: bool = False,
    ) -> None:
        super().__init__(fuzzy=fuzzy)
        self._suggestions = suggestion_callable
        self.update()

    def suggestions(self) -> list[str]:
        return list(self._suggestions())
