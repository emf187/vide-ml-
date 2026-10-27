"""
LLM API client with context-aware query handling.

Supports Groq (default) and any OpenAI-compatible API endpoint.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Optional

import httpx

from config.settings import get_settings
# Import ContextBuilder from either the package namespace or a local module.
# Try multiple import paths to accommodate different runtime layouts and
# IDE/static-analysis setups.
ContextBuilder = None
for _mod in ("llm_integration.context_builder", "context_builder"):
    try:
        mod = __import__(_mod, fromlist=["ContextBuilder"])
        ContextBuilder = getattr(mod, "ContextBuilder")
        break
    except Exception:
        ContextBuilder = None

if ContextBuilder is None:
    raise ImportError("Could not import ContextBuilder from 'llm_integration.context_builder' or 'context_builder'")

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = (
    "You are Vide, an expert industrial maintenance AI assistant. "
    "You analyze real-time sensor data, fault detection outputs, and equipment history "
    "to provide accurate diagnostics, root-cause analysis, maintenance recommendations, "
    "and operational insights.\n\n"
    "Guidelines:\n"
    "- Be precise with numbers — reference actual readings and thresholds.\n"
    "- Explain electrical and mechanical concepts in plain language when appropriate.\n"
    "- Always prioritize safety-critical findings.\n"
    "- Structure responses with clear headings when multiple topics are covered.\n"
    "- If data is insufficient, say so explicitly rather than guessing.\n"
    "- Keep responses actionable — every diagnosis should have a next step.\n"
)


class LLMClient:
    """
    LLM client that integrates with the fault detection engine.

    Automatically injects relevant system context based on query content,
    then sends the enriched prompt to the configured LLM provider.
    """

    def __init__(self) -> None:
        self._cfg = get_settings()
        self._context_builder = ContextBuilder()
        self._api_key: Optional[str] = None

    def _get_api_key(self) -> Optional[str]:
        """Read API key from environment variable."""
        if self._api_key is not None:
            return self._api_key
        self._api_key = os.environ.get(self._cfg.llm.api_key_env_var)
        return self._api_key

    def _detect_query_scope(self, message: str) -> str:
        """Heuristic: determine if query is about a specific machine or general."""
        # Check for machine name mentions (simple heuristic — can be enhanced)
        from storage.repositories import MachineRepository
        machines = MachineRepository.list_all()
        for m in machines:
            if m["machine_name"].lower() in message.lower():
                return m["machine_name"]
        return "__general__"

    def _build_messages(self, user_message: str) -> list[dict[str, str]]:
        """Build the message array with automatic context injection."""
        messages = [{"role": "system", "content": SYSTEM_PROMPT}]

        scope = self._detect_query_scope(user_message)

        if scope == "__general__":
            context = self._context_builder.build_general_context()
        else:
            context = self._context_builder.build_machine_context(scope)

        enriched_user = (
            f"<system_context>\n{context}\n</system_context>\n\n"
            f"<user_query>\n{user_message}\n</user_query>"
        )
        messages.append({"role": "user", "content": enriched_user})
        return messages

    def chat(self, message: str) -> dict[str, Any]:
        """
        Send a chat message with automatic context injection.

        Returns:
            Dict with "response" key containing the LLM reply, or "error" on failure.
        """
        if not self._cfg.llm.enabled:
            return {
                "response": "LLM is disabled. Set llm.enabled to true in config to enable AI analysis.",
                "error": "llm_disabled",
            }
        api_key = self._get_api_key()
        if not api_key:
            return {
                "response": (
                    "LLM is not configured. Set the environment variable "
                    f"{self._cfg.llm.api_key_env_var} to your API key."
                ),
                "error": "no_api_key",
            }

        messages = self._build_messages(message)

        try:
            with httpx.Client(timeout=self._cfg.llm.timeout_seconds) as client:
                resp = client.post(
                    self._cfg.llm.api_url,
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": self._cfg.llm.model,
                        "messages": messages,
                        "temperature": self._cfg.llm.temperature,
                        "max_tokens": self._cfg.llm.max_tokens,
                    },
                )
                resp.raise_for_status()
                data = resp.json()
                reply = data["choices"][0]["message"]["content"]
                return {"response": reply}

        except httpx.TimeoutException:
            logger.warning("LLM request timed out")
            return {"response": "The AI took too long to respond. Please try a shorter question.", "error": "timeout"}
        except httpx.ConnectError:
            logger.warning("LLM connection failed")
            return {"response": "Cannot reach the AI service. Check network connectivity.", "error": "connection"}
        except httpx.HTTPStatusError as e:
            detail = ""
            try:
                detail = e.response.json().get("error", {}).get("message", str(e))
            except Exception:
                detail = str(e)
            logger.error("LLM HTTP error: %s", detail)
            if "invalid_api_key" in detail.lower() or "unauthorized" in detail.lower():
                return {"response": "Invalid API key. Please verify your credentials.", "error": "auth"}
            return {"response": f"AI service error: {detail}", "error": "http"}
        except Exception as e:
            logger.exception("Unexpected LLM error")
            return {"response": f"Unexpected AI error: {e}", "error": "unknown"}

    def analyze_reading(self, reading: dict, detection_result: Any, fused: Any) -> dict[str, Any]:
        """
        Ask the LLM to analyze a specific reading, its rule-engine output, and
        the fused rule+ML diagnosis.

        This is the deep integration point — the LLM sees every rule evaluation,
        the ML prediction (when one exists for this reading), whether the two
        agree, and derived metrics/thresholds, to produce expert analysis.
        """
        if not self._cfg.llm.enabled:
            return {"response": "LLM is disabled. Set llm.enabled to true in config to enable AI analysis.", "error": "llm_disabled"}

        context = self._context_builder.build_reading_diagnosis_context(
            reading, detection_result, fused
        )

        enriched = (
            f"<system_context>\n{context}\n</system_context>\n\n"
            f"<task>\n"
            f"Analyze this reading and the fault detection engine output above. Provide:\n"
            f"1. Your assessment — do you agree with the engine's diagnosis?\n"
            f"2. Root cause analysis — what is the most likely underlying issue?\n"
            f"3. Immediate recommended actions (prioritized by urgency)\n"
            f"4. Any patterns or additional concerns not captured by the rules\n"
            f"</task>"
        )

        api_key = self._get_api_key()
        if not api_key:
            return {"response": "LLM not configured.", "error": "no_api_key"}

        try:
            with httpx.Client(timeout=self._cfg.llm.timeout_seconds) as client:
                resp = client.post(
                    self._cfg.llm.api_url,
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": self._cfg.llm.model,
                        "messages": [
                            {"role": "system", "content": SYSTEM_PROMPT},
                            {"role": "user", "content": enriched},
                        ],
                        "temperature": 0.3,  # Lower temperature for analytical tasks
                        "max_tokens": self._cfg.llm.max_tokens,
                    },
                )
                resp.raise_for_status()
                data = resp.json()
                return {"response": data["choices"][0]["message"]["content"]}
        except Exception as e:
            logger.exception("LLM reading analysis failed")
            return {"response": f"Analysis unavailable: {e}", "error": "unknown"}