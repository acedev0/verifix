"""Abstract interface for LLM patch generators."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Optional


class LLMClient(ABC):
    """Abstract base class for LLM patch generation backends."""

    @abstractmethod
    def generate_patch(self, prompt: str) -> str:
        """Generate candidate patch code given a prompt.

        Args:
            prompt: Formatted prompt containing buggy code, specifications, and counterexamples.

        Returns:
            String response containing candidate Python function.
        """
        pass
