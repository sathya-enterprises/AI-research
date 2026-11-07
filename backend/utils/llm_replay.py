"""Offline replay of captured LLM calls (no API credits).

ReplayGateway has the same ``generate()`` interface as utils.llm_gateway.LLMGateway
but answers from the ``llmCalls`` recorded in a research capture, matched by
(purpose, section id) in recorded order. It reports how many prompts differ
from the recorded ones, so a replay after a code change shows which calls
would have needed a fresh LLM response.
"""
from __future__ import annotations

from collections import defaultdict

from utils.llm_gateway import GatewayStats, LLMResult


class ReplayGateway:
    def __init__(self, capture: dict) -> None:
        self._calls: dict[tuple[str, str], list[dict]] = defaultdict(list)
        for call in capture.get("llmCalls") or []:
            if isinstance(call, dict):
                self._calls[(str(call.get("purpose") or ""), str(call.get("sectionId") or ""))].append(call)
        self._cursor: dict[tuple[str, str], int] = defaultdict(int)
        self.stats = GatewayStats()
        self.missing: list[str] = []
        self.prompt_changed: list[str] = []

    @property
    def recorded_calls(self) -> int:
        return sum(len(v) for v in self._calls.values())

    async def generate(self, *, purpose: str, prompt: str, system: str = "", section_id: str = "", **_kwargs) -> LLMResult:
        key = (purpose, section_id)
        recorded = self._calls.get(key) or []
        # A call that was retried is recorded per attempt; replay the last successful one in sequence.
        index = self._cursor[key]
        while index < len(recorded) and not recorded[index].get("ok") and index + 1 < len(recorded):
            index += 1
        self.stats.calls += 1
        self.stats.by_purpose[purpose] += 1
        if index >= len(recorded):
            self.missing.append(f"{purpose}/{section_id}")
            self.stats.failed += 1
            return LLMResult(ok=False, error="not_in_capture")
        call = recorded[index]
        self._cursor[key] = index + 1
        if str(call.get("llmPrompt") or "") != prompt:
            self.prompt_changed.append(f"{purpose}/{section_id}")
        ok = bool(call.get("ok")) and bool(call.get("llmResponse"))
        result = LLMResult(ok=ok, text=str(call.get("llmResponse") or ""), model=str(call.get("model") or "replay"),
                           error=str(call.get("error") or ""), tokens_in=int(call.get("tokensIn") or 0),
                           tokens_out=int(call.get("tokensOut") or 0), elapsed_ms=int(call.get("elapsedMs") or 0))
        self.stats.tokens_in += result.tokens_in
        self.stats.tokens_out += result.tokens_out
        if ok:
            self.stats.succeeded += 1
            self.stats.models[result.model] += 1
        else:
            self.stats.failed += 1
        return result
