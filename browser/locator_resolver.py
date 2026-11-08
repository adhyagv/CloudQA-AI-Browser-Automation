"""Resolve snapshot-scoped IDs through conservative semantic locators."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

from playwright.sync_api import Locator, Page

from browser.page_inspector import PageSnapshot, SnapshotElement


class ResolutionStatus(StrEnum):
    RESOLVED = "RESOLVED"
    NOT_FOUND = "NOT_FOUND"
    AMBIGUOUS = "AMBIGUOUS"
    HIDDEN = "HIDDEN"
    DISABLED = "DISABLED"


@dataclass(frozen=True)
class ResolutionResult:
    status: ResolutionStatus
    element: SnapshotElement | None = None
    locator: Locator | None = None
    candidates: tuple[str, ...] = ()
    message: str = ""


class LocatorResolver:
    """Resolve IDs from one snapshot without guessing among duplicate elements."""

    def __init__(self, page: Page) -> None:
        self.page = page

    def resolve(
        self,
        snapshot: PageSnapshot,
        element_id: str,
        *,
        require_enabled: bool = True,
    ) -> ResolutionResult:
        element = snapshot.get(element_id)
        if element is None:
            return ResolutionResult(
                ResolutionStatus.NOT_FOUND,
                message=f"Element ID {element_id} is not present in the current snapshot.",
            )

        ambiguous_candidates: set[str] = set()
        for strategy, locator in self._semantic_candidates(element):
            if locator is None:
                continue
            count = locator.count()
            if count > 1:
                ambiguous_candidates.update(self._candidate_ids(snapshot, element, strategy))
                continue
            if count == 0:
                continue
            return self._validate_single(
                element,
                locator,
                require_enabled=require_enabled,
            )

        fallback = self._stable_id_fallback(element)
        if fallback is not None and fallback.count() == 1:
            return self._validate_single(
                element,
                fallback,
                require_enabled=require_enabled,
            )

        if ambiguous_candidates:
            return ResolutionResult(
                ResolutionStatus.AMBIGUOUS,
                element=element,
                candidates=tuple(sorted(ambiguous_candidates)),
                message=(
                    f"Element ID {element_id} matches multiple page elements; "
                    "the target cannot be distinguished safely."
                ),
            )
        return ResolutionResult(
            ResolutionStatus.NOT_FOUND,
            element=element,
            message=f"Element ID {element_id} could not be found on the current page.",
        )

    def _semantic_candidates(
        self,
        element: SnapshotElement,
    ) -> tuple[tuple[str, Locator | None], ...]:
        role = (
            self.page.get_by_role(element.role, name=element.name, exact=True)
            if element.name and element.role != "text"
            else None
        )
        label = self.page.get_by_label(element.label, exact=True) if element.label else None
        placeholder = (
            self.page.get_by_placeholder(element.placeholder, exact=True)
            if element.placeholder
            else None
        )
        text = self.page.get_by_text(element.text, exact=True) if element.text else None
        return (
            ("role", role),
            ("label", label),
            ("placeholder", placeholder),
            ("text", text),
        )

    def _candidate_ids(
        self,
        snapshot: PageSnapshot,
        element: SnapshotElement,
        strategy: str,
    ) -> set[str]:
        field_by_strategy = {
            "role": "name",
            "label": "label",
            "placeholder": "placeholder",
            "text": "text",
        }
        field = field_by_strategy[strategy]
        value = getattr(element, field)
        return {
            candidate.element_id
            for candidate in snapshot.elements
            if getattr(candidate, field) == value
            and (strategy != "role" or candidate.role == element.role)
        }

    def _stable_id_fallback(self, element: SnapshotElement) -> Locator | None:
        if not element.stable_id or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_-]*", element.stable_id):
            return None
        return self.page.locator(f'[id="{element.stable_id}"]')

    @staticmethod
    def _validate_single(
        element: SnapshotElement,
        locator: Locator,
        *,
        require_enabled: bool,
    ) -> ResolutionResult:
        if not locator.is_visible():
            return ResolutionResult(
                ResolutionStatus.HIDDEN,
                element=element,
                locator=locator,
                message=f"Element ID {element.element_id} is not visible.",
            )
        if require_enabled and not locator.is_enabled():
            return ResolutionResult(
                ResolutionStatus.DISABLED,
                element=element,
                locator=locator,
                message=f"Element ID {element.element_id} is disabled.",
            )
        return ResolutionResult(
            ResolutionStatus.RESOLVED,
            element=element,
            locator=locator,
        )


__all__ = ["LocatorResolver", "ResolutionResult", "ResolutionStatus"]
