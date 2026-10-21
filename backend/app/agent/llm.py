"""LLM-провайдеры с единым интерфейсом для цикла tool use.

- AnthropicLLM     — Claude через Anthropic SDK (облако).
- OpenAICompatLLM  — on-prem: Ollama / vLLM / любой OpenAI-совместимый сервер (данные не покидают контур).

LLM не считает: она выбирает инструмент, извлекает параметры и формулирует вердикт по готовым числам.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from app.config import settings


@dataclass
class ToolCall:
    id: str
    name: str
    input: dict


@dataclass
class LLMTurn:
    text: str
    tool_calls: list[ToolCall]
    stop_reason: str
    raw_assistant: Any = None          # для возврата в историю в формате провайдера
    usage: dict = field(default_factory=dict)


class AnthropicLLM:
    def __init__(self):
        import anthropic
        self.client = anthropic.Anthropic(api_key=settings.anthropic_api_key or None)
        self.model = settings.anthropic_model

    def tool_schema(self, tools: list[dict]) -> list[dict]:
        return [{"name": t["name"], "description": t["description"], "input_schema": t["parameters"]} for t in tools]

    def start(self, system: str, user: str) -> list:
        return [{"role": "user", "content": user}]

    def step(self, system: str, messages: list, tools: list[dict]) -> LLMTurn:
        resp = self.client.beta.messages.create(
            model=self.model,
            max_tokens=16000,
            system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            tools=self.tool_schema(tools),
            messages=messages,
            thinking={"type": "adaptive"},
            output_config={"effort": "medium"},
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
        )
        calls = [ToolCall(b.id, b.name, b.input) for b in resp.content if b.type == "tool_use"]
        text = "".join(b.text for b in resp.content if b.type == "text")
        return LLMTurn(text, calls, resp.stop_reason, resp.content,
                       {"input": resp.usage.input_tokens, "output": resp.usage.output_tokens})

    def append(self, messages: list, turn: LLMTurn, results: list[tuple[ToolCall, str, bool]]) -> None:
        messages.append({"role": "assistant", "content": turn.raw_assistant})
        messages.append({"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": c.id, "content": out, **({"is_error": True} if err else {})}
            for c, out, err in results]})

    def nudge(self, messages: list, turn: LLMTurn, text: str) -> None:
        messages.append({"role": "assistant", "content": turn.raw_assistant})
        messages.append({"role": "user", "content": text})


class OpenAICompatLLM:
    def __init__(self):
        from openai import OpenAI
        self.client = OpenAI(base_url=settings.openai_compat_base_url, api_key=settings.openai_compat_api_key,
                             max_retries=settings.openai_compat_max_retries, timeout=120)
        self.model = settings.openai_compat_model

    def tool_schema(self, tools: list[dict]) -> list[dict]:
        return [{"type": "function", "function": {"name": t["name"], "description": t["description"],
                                                  "parameters": t["parameters"]}} for t in tools]

    def start(self, system: str, user: str) -> list:
        return [{"role": "system", "content": system}, {"role": "user", "content": user}]

    def step(self, system: str, messages: list, tools: list[dict]) -> LLMTurn:
        extra = {} if settings.openai_compat_temperature is None else {"temperature": settings.openai_compat_temperature}
        resp = self.client.chat.completions.create(model=self.model, messages=messages,
                                                   tools=self.tool_schema(tools), **extra)
        m = resp.choices[0].message
        calls = []
        for tc in m.tool_calls or []:
            try:
                args = json.loads(tc.function.arguments or "{}")
            except json.JSONDecodeError:
                args = {"_invalid_json": tc.function.arguments}
            calls.append(ToolCall(tc.id, tc.function.name, args))
        usage = {"input": resp.usage.prompt_tokens, "output": resp.usage.completion_tokens} if resp.usage else {}
        return LLMTurn(m.content or "", calls, resp.choices[0].finish_reason, m, usage)

    def append(self, messages: list, turn: LLMTurn, results: list[tuple[ToolCall, str, bool]]) -> None:
        # сообщение ассистента возвращается целиком: провайдеры кладут туда служебные поля
        # (например, Gemini — extra_content.google.thought_signature у tool_calls), без них следующий шаг падает
        messages.append(self._assistant(turn))
        for c, out, _ in results:
            messages.append({"role": "tool", "tool_call_id": c.id, "content": out})

    def nudge(self, messages: list, turn: LLMTurn, text: str) -> None:
        messages.append(self._assistant(turn))
        messages.append({"role": "user", "content": text})

    @staticmethod
    def _assistant(turn: LLMTurn) -> dict:
        d = turn.raw_assistant.model_dump(exclude_none=True)
        d["content"] = d.get("content") or ""
        return d


def get_llm():
    if settings.llm_provider == "openai_compat":
        return OpenAICompatLLM()
    return AnthropicLLM()
