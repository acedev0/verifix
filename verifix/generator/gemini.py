"""Google Gemini LLM provider for VeriFix."""

from __future__ import annotations

import json
import os
from typing import Optional
import requests

from verifix.generator.llm_client import LLMClient


class GeminiClient(LLMClient):
    """LLM client connecting to Google Gemini models."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: str = "gemini-2.5-flash",
        temperature: float = 0.2,
    ) -> None:
        self.api_key = api_key or os.environ.get("GEMINI_API_KEY")
        self.model = model
        self.temperature = temperature
        self._genai_client = None

        if self.api_key:
            try:
                from google import genai
                self._genai_client = genai.Client(api_key=self.api_key)
            except ImportError:
                self._genai_client = None

    def generate_patch(self, prompt: str) -> str:
        """Call Gemini to generate a candidate patch proposal."""
        if not self.api_key:
            raise ValueError(
                "Gemini API key not found. Please set the GEMINI_API_KEY environment variable "
                "or pass api_key to GeminiClient."
            )

        # 1. Prefer google-genai SDK if available
        if self._genai_client is not None:
            try:
                response = self._genai_client.models.generate_content(
                    model=self.model,
                    contents=prompt,
                )
                if response and response.text:
                    return response.text
            except Exception as e:
                # Fallback to direct HTTP request on error
                pass

        # 2. Direct REST API request using requests
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent?key={self.api_key}"
        headers = {"Content-Type": "application/json"}
        payload = {
            "contents": [
                {
                    "parts": [{"text": prompt}]
                }
            ],
            "generationConfig": {
                "temperature": self.temperature,
                "maxOutputTokens": 2048,
            }
        }

        resp = requests.post(url, headers=headers, json=payload, timeout=30)
        resp.raise_for_status()
        data = resp.json()

        try:
            candidates = data.get("candidates", [])
            if candidates:
                content = candidates[0].get("content", {})
                parts = content.get("parts", [])
                if parts:
                    return parts[0].get("text", "")
        except Exception as e:
            raise RuntimeError(f"Failed to parse Gemini response: {e}")

        raise RuntimeError("No text content returned in Gemini response.")
