"""The single wrapper for every LLM call: Groq, retries, timeout, JSON output, fallback cache.

Every successful live answer is recorded under a stable key, so the same demo
step can be replayed offline. If Groq fails and no recording exists, callers get
`None` and use their deterministic template.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import time
from pathlib import Path
from typing import Any

from groq import AsyncGroq

log = logging.getLogger("utilityguard.llm")

DATA = Path(__file__).resolve().parents[2] / "data"
CACHE_FILE = DATA / "fallback_cache.json"
ENV_FILE = Path(__file__).resolve().parents[2] / ".env"


def _load_env() -> None:
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text().splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())


_load_env()
MODEL_ORCH = os.getenv("GROQ_MODEL_ORCHESTRATOR", "openai/gpt-oss-120b")
MODEL_FAST = os.getenv("GROQ_MODEL_SPECIALIST", "openai/gpt-oss-20b")


class LLM:
    def __init__(self) -> None:
        key = os.getenv("GROQ_API_KEY")
        self.forced_offline = os.getenv("LLM_OFFLINE") == "1"
        self.client = AsyncGroq(api_key=key, timeout=12.0, max_retries=0) if key and not self.forced_offline else None
        self.cache: dict[str, Any] = json.loads(CACHE_FILE.read_text()) if CACHE_FILE.exists() else {}
        self.last_ok: bool = self.client is not None
        self.calls: list[dict] = []  # audit log of every call

    @property
    def mode(self) -> str:
        return "live" if self.client and self.last_ok else "fallback"

    @staticmethod
    def key(*parts: str) -> str:
        return hashlib.sha1("|".join(parts).encode()).hexdigest()[:16]

    def _save(self) -> None:
        DATA.mkdir(parents=True, exist_ok=True)
        CACHE_FILE.write_text(json.dumps(self.cache, indent=1, ensure_ascii=False))

    async def json(self, cache_key: str, system: str, user: str, model: str = MODEL_FAST,
                   retries: int = 2, temperature: float = 0.2) -> tuple[dict | None, bool]:
        """Return (data, fallback_used). data is None if both live and cache fail."""
        started = time.time()
        if self.client:
            for attempt in range(retries + 1):
                try:
                    r = await self.client.chat.completions.create(
                        model=model, temperature=temperature, response_format={"type": "json_object"},
                        messages=[{"role": "system", "content": system}, {"role": "user", "content": user}])
                    data = json.loads(r.choices[0].message.content)
                    self.last_ok = True
                    self.cache[cache_key] = data
                    self._save()
                    self.calls.append({"key": cache_key, "model": model, "ok": True, "ms": int((time.time() - started) * 1000)})
                    log.info("llm ok model=%s key=%s %dms", model, cache_key, (time.time() - started) * 1000)
                    return data, False
                except Exception as e:  # noqa: BLE001 - any failure falls back
                    log.warning("llm attempt %d failed: %s", attempt + 1, e)
                    if attempt < retries:
                        await asyncio.sleep(0.6 * (attempt + 1))
            self.last_ok = False
        self.calls.append({"key": cache_key, "model": model, "ok": False, "ms": int((time.time() - started) * 1000)})
        if cache_key in self.cache:
            log.info("llm fallback cache hit key=%s", cache_key)
            return self.cache[cache_key], True
        return None, True


llm = LLM()
