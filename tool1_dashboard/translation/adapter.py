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


class TranslationAdapter:
    """Calls Gemini / OpenAI / Anthropic translation APIs."""

    async def translate_chunk(
        self,
        provider: str,
        api_key: str,
        model: str,
        prompt: str,
    ) -> str:
        if provider == "gemini":
            return await self._call_gemini(api_key, model, prompt)
        if provider == "openai":
            return await self._call_openai(api_key, model, prompt)
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

    async def _call_openai(self, api_key: str, model: str, prompt: str) -> str:
        url = "https://api.openai.com/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        body = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": _TEMPERATURE,
            "max_tokens": _MAX_TOKENS,
        }
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.post(url, headers=headers, json=body)
        if resp.status_code != 200:
            msg = self._extract_error(resp, "error", "message")
            raise TranslationError("OpenAI", resp.status_code, msg)
        data = resp.json()
        try:
            return data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError):
            return ""

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
