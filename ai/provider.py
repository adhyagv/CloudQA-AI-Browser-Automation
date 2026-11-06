"""Provider interface used by the AI planner."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TypeVar

from pydantic import BaseModel

SchemaT = TypeVar("SchemaT", bound=BaseModel)


class PlannerProvider(ABC):
    """Generate a response that conforms to the supplied Pydantic schema."""

    @abstractmethod
    def generate(self, prompt: str, *, response_schema: type[SchemaT]) -> str:
        """Return a structured response as JSON text."""


__all__ = ["PlannerProvider"]
