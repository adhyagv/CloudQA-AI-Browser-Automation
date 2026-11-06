"""Scripted offline provider for tests and local planner demonstrations."""

from __future__ import annotations

from collections.abc import Iterable
from typing import TypeVar

from pydantic import BaseModel

from ai.provider import PlannerProvider

SchemaT = TypeVar("SchemaT", bound=BaseModel)


class FakeProvider(PlannerProvider):
    """Return queued responses and retain prompts for offline assertions."""

    def __init__(self, responses: Iterable[str]) -> None:
        self._responses = iter(responses)
        self.prompts: list[str] = []
        self.response_schemas: list[type[BaseModel]] = []

    def generate(self, prompt: str, *, response_schema: type[SchemaT]) -> str:
        self.prompts.append(prompt)
        self.response_schemas.append(response_schema)
        try:
            return next(self._responses)
        except StopIteration as exc:
            raise RuntimeError("FakeProvider has no scripted response remaining") from exc


__all__ = ["FakeProvider"]
