"""Ollama LLM provider for local model inference.

Phase 4 — Model Router: OllamaProvider
Provides LLM completions and embeddings via a local Ollama server.
Cross-reference: MASTER_REFERENCE.md, PHASE_04_MODEL_ROUTER.md
"""

from __future__ import annotations

import json
import logging
from typing import Any

import httpx

logger = logging.getLogger("pawbot")


class OllamaProvider:
    """Provides LLM completions and embeddings via local Ollama server.

    Base URL: http://localhost:11434 (configurable via config.json)

    Config path: config["providers"]["ollama"]
    Keys:
        base_url       — Ollama server URL (default: http://localhost:11434)
        default_model  — Default chat model (default: llama3.1:8b)
        embedding_model — Embedding model (default: nomic-embed-text)
        coding_model   — Coding-focused model (default: deepseek-coder:6.7b)
    """

    DEFAULT_BASE_URL = "http://localhost:11434"

    def __init__(self, config: dict[str, Any]) -> None:
        ollama_cfg = config.get("providers", {}).get("ollama", {})
        self.base_url = ollama_cfg.get("base_url", self.DEFAULT_BASE_URL).rstrip("/")
        self.default_model = ollama_cfg.get("default_model", "llama3.1:8b")
        self.embedding_model = ollama_cfg.get("embedding_model", "nomic-embed-text")
        self.coding_model = ollama_cfg.get("coding_model", "deepseek-coder:6.7b")
        self._available_models: list[str] = []

    def is_available(self) -> bool:
        """Check if Ollama server is reachable."""
        try:
            r = httpx.get(f"{self.base_url}/api/tags", timeout=3.0)
            return r.status_code == 200
        except Exception as e:  # noqa: F841
            return False

    def get_available_models(self) -> list[str]:
        """Returns list of model names currently pulled in Ollama."""
        try:
            r = httpx.get(f"{self.base_url}/api/tags", timeout=5.0)
            data = r.json()
            self._available_models = [
                m["name"] for m in data.get("models", [])
            ]
            return self._available_models
        except Exception as e:
            logger.warning("Ollama model list failed: %s", e)
            return []

    def ensure_model(self, model_name: str) -> None:
        """Auto-pull model if not available. Blocks until complete."""
        available = self.get_available_models()
        # Check if model name matches any available (Ollama uses "name:tag" format)
        if any(model_name in m for m in available):
            return

        logger.info("Ollama: pulling model %s...", model_name)
        try:
            with httpx.stream(
                "POST",
                f"{self.base_url}/api/pull",
                json={"name": model_name},
                timeout=300.0,
            ) as r:
                for line in r.iter_lines():
                    if line:
                        try:
                            progress = json.loads(line)
                            if progress.get("status") == "success":
                                logger.info("Ollama: model %s ready", model_name)
                                break
                        except json.JSONDecodeError:
                            pass
        except Exception as e:
            logger.warning("Ollama: model pull failed for %s: %s", model_name, e)

    def complete(self, model: str, prompt: str, system: str = "") -> str:
        """Synchronous completion via /api/generate.

        Returns the generated text string.
        """
        self.ensure_model(model)

        payload: dict[str, Any] = {
            "model": model,
            "prompt": prompt,
            "stream": False,
        }
        if system:
            payload["system"] = system

        try:
            r = httpx.post(
                f"{self.base_url}/api/generate",
                json=payload,
                timeout=120.0,
            )
            r.raise_for_status()
            return r.json().get("response", "")
        except Exception as e:
            logger.error("Ollama complete failed: %s", e)
            raise

    def chat(self, model: str, messages: list[dict[str, str]]) -> str:
        """Chat completion via /api/chat.

        Returns the assistant's response text.
        """
        self.ensure_model(model)

        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "stream": False,
        }

        try:
            r = httpx.post(
                f"{self.base_url}/api/chat",
                json=payload,
                timeout=120.0,
            )
            r.raise_for_status()
            data = r.json()
            return data.get("message", {}).get("content", "")
        except Exception as e:
            logger.error("Ollama chat failed: %s", e)
            raise

    def embed(self, text: str) -> list[float]:
        """Returns embedding vector for text using embedding_model."""
        self.ensure_model(self.embedding_model)
        try:
            r = httpx.post(
                f"{self.base_url}/api/embeddings",
                json={"model": self.embedding_model, "prompt": text},
                timeout=30.0,
            )
            r.raise_for_status()
            return r.json().get("embedding", [])
        except Exception as e:
            logger.error("Ollama embed failed: %s", e)
            raise
