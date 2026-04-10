"""Translation API adapters — ported from TRADUTOR app.js API callers."""

from __future__ import annotations

import httpx


class TranslationError(Exception):
    """Raised when a translation API call fails."""

    def __init__(self, provider: str, status: int, message: str) -> None:
        self.provider = provider
        self.status = status
        super().__init__(f"{provider} error ({status}): {message}")


_TIMEOUT = httpx.Timeout(120.0, connect=30.0)
_TEMPERATURE = 0.3
_MAX_TOKENS = 8192
_OPENAI_RETRY_MAX_TOKENS = 16384


class TranslationAdapter:
    """Calls Gemini / OpenAI / Anthropic translation APIs."""

    async def translate_chunk(
        self,
        provider: str,
        api_key: str,
        model: str,
        prompt: str,
        *,
        base_url: str | None = None,
    ) -> str:
        if provider == "gemini":
            return await self._call_gemini(api_key, model, prompt)
        if provider == "openai":
            return await self._call_openai(
                api_key,
                model,
                prompt,
                base_url=base_url or "https://api.openai.com/v1",
                provider_label="OpenAI",
                require_api_key=True,
            )
        if provider == "openai_compatible":
            return await self._call_openai(
                api_key,
                model,
                prompt,
                base_url=base_url or "http://127.0.0.1:1234/v1",
                provider_label="OpenAI-compatible",
                require_api_key=False,
            )
        if provider == "anthropic":
            return await self._call_anthropic(api_key, model, prompt)
        raise ValueError(f"Unknown translation provider: {provider}")

    async def _call_gemini(self, api_key: str, model: str, prompt: str) -> str:
        model_id = model.replace("models/", "") if model.startswith("models/") else model
        url = (
            f"https://generativelanguage.googleapis.com/v1beta/models/"
            f"{model_id}:generateContent?key={api_key}"
        )
        body = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "temperature": _TEMPERATURE,
                "maxOutputTokens": _MAX_TOKENS,
            },
        }
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.post(url, json=body)
        if resp.status_code != 200:
            msg = self._extract_error(resp, "error", "message")
            raise TranslationError("Gemini", resp.status_code, msg)
        data = resp.json()
        try:
            return data["candidates"][0]["content"]["parts"][0]["text"]
        except (KeyError, IndexError, TypeError):
            return ""

    async def _call_openai(
        self,
        api_key: str,
        model: str,
        prompt: str,
        *,
        base_url: str,
        provider_label: str,
        require_api_key: bool,
    ) -> str:
        resolved_base_url = str(base_url or "").strip().rstrip("/")
        if not resolved_base_url:
            raise TranslationError(provider_label, 500, "Missing OpenAI-compatible base URL.")
        if require_api_key and not str(api_key or "").strip():
            raise TranslationError(provider_label, 500, "Missing API key.")
        url = f"{resolved_base_url}/responses"
        headers = {"Content-Type": "application/json"}
        if str(api_key or "").strip():
            headers["Authorization"] = f"Bearer {api_key}"
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            body = self._build_openai_request_body(
                model=model,
                prompt=prompt,
                max_output_tokens=_MAX_TOKENS,
            )
            resp = await client.post(url, headers=headers, json=body)
            if resp.status_code == 200:
                data = resp.json()
                if (
                    str(data.get("status") or "").strip().lower() == "incomplete"
                    and self._extract_openai_incomplete_reason(data) == "max_output_tokens"
                    and _OPENAI_RETRY_MAX_TOKENS > _MAX_TOKENS
                ):
                    retry_body = self._build_openai_request_body(
                        model=model,
                        prompt=prompt,
                        max_output_tokens=_OPENAI_RETRY_MAX_TOKENS,
                    )
                    resp = await client.post(url, headers=headers, json=retry_body)
        if resp.status_code != 200:
            msg = self._extract_error(resp, "error", "message")
            raise TranslationError(provider_label, resp.status_code, msg)
        data = resp.json()
        if str(data.get("status") or "").strip().lower() == "incomplete":
            reason = self._extract_openai_incomplete_reason(data)
            raise TranslationError(provider_label, resp.status_code, f"Response incomplete: {reason}")

        text = self._extract_openai_output_text(data)
        if text:
            return text

        output_types = ", ".join(
            str(item.get("type") or "").strip()
            for item in data.get("output") or []
            if isinstance(item, dict) and str(item.get("type") or "").strip()
        )
        detail = f" (output types: {output_types})" if output_types else ""
        raise TranslationError(provider_label, resp.status_code, f"Empty response from model{detail}")

    async def _call_anthropic(self, api_key: str, model: str, prompt: str) -> str:
        url = "https://api.anthropic.com/v1/messages"
        headers = {
            "Content-Type": "application/json",
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
        }
        body = {
            "model": model,
            "max_tokens": _MAX_TOKENS,
            "messages": [{"role": "user", "content": prompt}],
        }
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.post(url, headers=headers, json=body)
        if resp.status_code != 200:
            msg = self._extract_error(resp, "error", "message")
            raise TranslationError("Anthropic", resp.status_code, msg)
        data = resp.json()
        try:
            return data["content"][0]["text"]
        except (KeyError, IndexError, TypeError):
            return ""

    @staticmethod
    def _extract_error(resp: httpx.Response, *keys: str) -> str:
        try:
            data = resp.json()
            value = data
            for key in keys:
                value = value[key]
            return str(value)
        except Exception:
            return resp.text[:300] if resp.text else f"HTTP {resp.status_code}"

    @staticmethod
    def _build_openai_request_body(
        *,
        model: str,
        prompt: str,
        max_output_tokens: int = _MAX_TOKENS,
    ) -> dict[str, object]:
        body: dict[str, object] = {
            "model": model,
            "input": prompt,
            "max_output_tokens": max_output_tokens,
        }
        normalized_model = str(model or "").strip().lower()
        if normalized_model.startswith("gpt-5"):
            # Keep GPT-5 translation runs focused on the final text instead of
            # spending the chunk budget on internal reasoning.
            effort = "low" if normalized_model.startswith("gpt-5.4") else "minimal"
            body["reasoning"] = {"effort": effort}
            body["text"] = {"verbosity": "low"}
        return body

    @staticmethod
    def _extract_openai_output_text(data: dict[str, object]) -> str:
        output_text = data.get("output_text")
        if isinstance(output_text, str) and output_text.strip():
            return output_text.strip()

        try:
            text = "".join(
                str(item.get("text") or "")
                for output in data.get("output") or []
                if isinstance(output, dict)
                for item in output.get("content") or []
                if isinstance(item, dict) and item.get("type") == "output_text"
            ).strip()
        except (AttributeError, TypeError):
            text = ""
        return text

    @staticmethod
    def _extract_openai_incomplete_reason(data: dict[str, object]) -> str:
        details = data.get("incomplete_details")
        if isinstance(details, dict):
            reason = str(details.get("reason") or "").strip()
            if reason:
                return reason
        return "unknown"
