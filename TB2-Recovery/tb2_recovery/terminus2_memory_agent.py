from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from tb2_recovery.harbor_path import ensure_harbor_site_packages


ensure_harbor_site_packages()

from harbor.agents.terminus_2 import Terminus2


class RecoveryTerminus2(Terminus2):
    """Terminus2 with Recovery-Bench memory carried as native chat history."""

    def __init__(
        self,
        *args,
        recovery_chat_messages_path: str | None = None,
        recovery_chat_messages_text: str | None = None,
        recovery_memory_path: str | None = None,
        recovery_memory_text: str | None = None,
        dynamic_max_tokens: bool = True,
        output_token_safety_margin: int = 2048,
        **kwargs,
    ):
        super().__init__(*args, **kwargs)
        self._recovery_chat_messages_path = recovery_chat_messages_path
        self._recovery_chat_messages_text = recovery_chat_messages_text
        self._recovery_memory_path = recovery_memory_path
        self._recovery_memory_text = recovery_memory_text
        self._dynamic_max_tokens = dynamic_max_tokens
        self._output_token_safety_margin = max(0, int(output_token_safety_margin))
        self._recovery_chat_messages_loaded = False

    async def run(self, instruction, environment, context) -> None:
        sections = [instruction.rstrip()]
        if not self._has_recovery_chat_messages():
            memory = self._load_recovery_memory()
        else:
            memory = ""
        if memory:
            sections.append(
                "Recovery-Bench memory from previous failed attempts:\n"
                + memory.strip()
                + "\n\n"
                + "Continue from the current terminal state and use the memory above only as your own prior attempt history."
            )
        instruction = "\n\n".join(section for section in sections if section)
        await super().run(instruction, environment, context)

    async def _run_agent_loop(self, initial_prompt, chat, logging_dir=None, original_instruction: str = "") -> None:
        self._load_recovery_chat_into(chat)
        await super()._run_agent_loop(initial_prompt, chat, logging_dir, original_instruction)

    async def _query_llm(
        self,
        chat,
        prompt: str,
        logging_paths,
        original_instruction: str = "",
        session=None,
    ):
        if not self._dynamic_max_tokens:
            return await super()._query_llm(chat, prompt, logging_paths, original_instruction, session)

        original_kwargs = dict(self._llm_call_kwargs)
        try:
            requested = original_kwargs.get("max_tokens")
            if requested is not None:
                cap = self._dynamic_output_cap(chat, prompt, int(requested))
                adjusted = dict(original_kwargs)
                adjusted["max_tokens"] = cap
                self._llm_call_kwargs = adjusted
            return await super()._query_llm(chat, prompt, logging_paths, original_instruction, session)
        finally:
            self._llm_call_kwargs = original_kwargs

    def _dynamic_output_cap(self, chat, prompt: str, requested: int) -> int:
        if requested <= 0:
            return requested
        try:
            from litellm.utils import token_counter

            context_limit = int(self._llm.get_model_context_limit())
            messages = list(chat.messages) + [{"role": "user", "content": prompt}]
            prompt_tokens = int(token_counter(model=self._model_name, messages=messages))
            available = context_limit - prompt_tokens - self._output_token_safety_margin
        except Exception:
            return requested
        if available <= 0:
            return 1
        return max(1, min(requested, available))

    def _load_recovery_memory(self) -> str:
        if self._recovery_memory_text:
            return self._recovery_memory_text
        if not self._recovery_memory_path:
            return ""
        path = Path(self._recovery_memory_path)
        try:
            return path.read_text(encoding="utf-8")
        except OSError:
            return ""

    def _has_recovery_chat_messages(self) -> bool:
        return bool(self._recovery_chat_messages_text or self._recovery_chat_messages_path)

    def _load_recovery_chat_messages(self) -> list[dict[str, Any]]:
        raw = self._recovery_chat_messages_text
        if raw is None and self._recovery_chat_messages_path:
            path = Path(self._recovery_chat_messages_path)
            try:
                raw = path.read_text(encoding="utf-8")
            except OSError:
                raw = None
        if not raw:
            return []
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return []
        if not isinstance(data, list):
            return []
        messages: list[dict[str, Any]] = []
        for item in data:
            if not isinstance(item, dict):
                continue
            role = item.get("role")
            content = item.get("content")
            if role not in {"user", "assistant", "system"} or not isinstance(content, str):
                continue
            message: dict[str, Any] = {"role": role, "content": content}
            if role == "assistant" and isinstance(item.get("reasoning_content"), str):
                message["reasoning_content"] = item["reasoning_content"]
            messages.append(message)
        return messages

    def _load_recovery_chat_into(self, chat) -> None:
        if self._recovery_chat_messages_loaded:
            return
        self._recovery_chat_messages_loaded = True
        messages = self._load_recovery_chat_messages()
        if not messages:
            return

        chat.messages.extend(messages)
        chat.reset_response_chain()

        if not hasattr(self, "_convert_chat_messages_to_steps"):
            return
        copied_steps = self._convert_chat_messages_to_steps(messages, mark_as_copied=True)
        current_steps = list(getattr(self, "_trajectory_steps", []))
        for index, step in enumerate(copied_steps + current_steps, start=1):
            step.step_id = index
        self._trajectory_steps = copied_steps + current_steps
