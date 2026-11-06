"""Compact, value-free page snapshots with snapshot-scoped element IDs."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from typing import Any

from playwright.sync_api import Locator, Page

from config.settings import Settings, load_settings

_INTERACTIVE_SELECTOR = "a, button, input, select, textarea, [role=alert], [role=status]"
_ROLE_BY_TAG = {
    "a": "link",
    "button": "button",
    "select": "combobox",
    "textarea": "textbox",
}


@dataclass(frozen=True)
class SnapshotElement:
    element_id: str
    role: str
    name: str
    label: str
    placeholder: str
    text: str
    input_type: str
    enabled: bool
    visible: bool
    checked: bool | None
    masked: bool
    locator: Locator = field(repr=False, compare=False)
    stable_id: str = ""
    tag: str = ""

    def public_data(self) -> dict[str, Any]:
        return {
            "id": self.element_id,
            "role": self.role,
            "name": self.name,
            "label": self.label,
            "placeholder": self.placeholder,
            "text": self.text,
            "input_type": self.input_type,
            "enabled": self.enabled,
            "visible": self.visible,
            "checked": self.checked,
            "masked": self.masked,
        }


@dataclass(frozen=True)
class PageSnapshot:
    token: str
    url: str
    title: str
    headings: tuple[str, ...]
    elements: tuple[SnapshotElement, ...]

    def get(self, element_id: str) -> SnapshotElement | None:
        return next((element for element in self.elements if element.element_id == element_id), None)

    def to_dict(self) -> dict[str, Any]:
        return {
            "url": self.url,
            "title": self.title,
            "headings": list(self.headings),
            "elements": [element.public_data() for element in self.elements],
        }

    def compact_text(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, separators=(",", ":"))


class PageInspector:
    """Inspect only visible controls and never read form-control values."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or load_settings()

    def inspect(self, page: Page) -> PageSnapshot:
        headings = self._headings(page)
        locators = page.locator(_INTERACTIVE_SELECTOR)
        total = locators.count()
        elements: list[SnapshotElement] = []

        for index in range(total):
            locator = locators.nth(index)
            if not locator.is_visible():
                continue
            tag = locator.evaluate("(element) => element.tagName.toLowerCase()")
            input_type = (locator.get_attribute("type") or "").lower()
            if tag == "input" and input_type == "hidden":
                continue

            stable_id = locator.get_attribute("id") or ""
            label = self._associated_label(page, locator, stable_id)
            placeholder = locator.get_attribute("placeholder") or ""
            aria_label = locator.get_attribute("aria-label") or ""
            aria_labelledby = self._aria_labelledby_text(locator)
            title = locator.get_attribute("title") or ""
            explicit_role = locator.get_attribute("role") or ""
            text = self._visible_text(locator, tag, explicit_role)
            role = explicit_role or self._role(tag, input_type)
            name = self._truncate(
                aria_labelledby or aria_label or label or title or placeholder or text
            )

            checked: bool | None = None
            if tag == "input" and input_type in {"checkbox", "radio"}:
                checked = locator.is_checked()

            elements.append(
                SnapshotElement(
                    element_id=f"e{len(elements) + 1}",
                    role=role,
                    name=name,
                    label=self._truncate(label),
                    placeholder=self._truncate(placeholder),
                    text=self._truncate(text),
                    input_type=input_type,
                    enabled=locator.is_enabled(),
                    visible=True,
                    checked=checked,
                    masked=tag == "input" and input_type == "password",
                    locator=locator,
                    stable_id=stable_id,
                    tag=tag,
                )
            )
            if len(elements) >= self.settings.snapshot_max_elements:
                break

        return PageSnapshot(
            token=uuid.uuid4().hex,
            url=page.url,
            title=self._truncate(page.title()),
            headings=headings,
            elements=tuple(elements),
        )

    def _headings(self, page: Page) -> tuple[str, ...]:
        headings: list[str] = []
        locator = page.locator("h1, h2, h3")
        for index in range(locator.count()):
            heading = locator.nth(index)
            if heading.is_visible():
                text = self._truncate(heading.inner_text())
                if text:
                    headings.append(text)
        return tuple(headings)

    def _associated_label(self, page: Page, locator: Locator, stable_id: str) -> str:
        labels = page.locator("label")
        for index in range(labels.count()):
            label = labels.nth(index)
            if stable_id and label.get_attribute("for") == stable_id:
                return label.inner_text().strip()
        return locator.evaluate(
            "(element) => Array.from(element.labels || [])"
            ".map((label) => label.innerText).join(' ').trim()"
        )

    @staticmethod
    def _aria_labelledby_text(locator: Locator) -> str:
        if not locator.get_attribute("aria-labelledby"):
            return ""
        return locator.evaluate(
            "(element) => (element.getAttribute('aria-labelledby') || '')"
            ".split(/\\s+/)"
            ".map((id) => element.ownerDocument.getElementById(id)?.innerText || '')"
            ".join(' ').trim()"
        )

    def _visible_text(self, locator: Locator, tag: str, role: str) -> str:
        if tag in {"a", "button"} or role in {"alert", "status"}:
            return locator.inner_text().strip()
        return ""

    @staticmethod
    def _role(tag: str, input_type: str) -> str:
        if tag == "input":
            if input_type in {"button", "submit", "reset", "image"}:
                return "button"
            if input_type == "checkbox":
                return "checkbox"
            if input_type == "radio":
                return "radio"
            if input_type == "search":
                return "searchbox"
            return "textbox"
        return _ROLE_BY_TAG.get(tag, tag)

    def _truncate(self, text: str) -> str:
        normalized = " ".join(text.split())
        limit = self.settings.snapshot_text_length
        if len(normalized) <= limit:
            return normalized
        return normalized[: max(0, limit - 1)].rstrip() + "…"


__all__ = ["PageInspector", "PageSnapshot", "SnapshotElement"]
