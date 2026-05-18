"""LLM-based ASR error correction via OpenAI-compatible API."""

import json
import logging
import urllib.request
import urllib.error

logger = logging.getLogger("voice-input.llm")

SYSTEM_PROMPT = """\
You are a conservative speech recognition error corrector. \
ONLY fix clear, obvious transcription mistakes. When in doubt, leave the text unchanged.

What to fix:
- English words/acronyms wrongly rendered as Chinese characters \
(e.g. "配森" → "Python", "杰森" → "JSON", "阿皮爱" → "API")
- Obvious Chinese homophone errors where context makes the correct character clear
- Broken English words or phrases split/merged incorrectly by the recognizer

What NOT to do:
- Do NOT rephrase, rewrite, or "improve" any text
- Do NOT add or remove words beyond fixing recognition errors
- Do NOT change text that could plausibly be correct
- Do NOT alter punctuation unless clearly wrong

If the input appears correct, return it exactly as-is. Return ONLY the text, nothing else."""


def _get_ls():
    """Lazy-import Logger (avoid module-level side effects)."""
    from voice_input.config import get_config
    return get_config


class LLMRefiner:
    def __init__(self):
        cfg = _get_ls()()

    @property
    def enabled(self):
        return _get_ls()().get("llm_enabled", False)

    @enabled.setter
    def enabled(self, v):
        self._save("llm_enabled", v)

    @property
    def api_base_url(self):
        return _get_ls()().get("llm_api_base_url", "https://api.openai.com/v1")

    @property
    def api_key(self):
        return _get_ls()().get("llm_api_key", "")

    @property
    def model(self):
        return _get_ls()().get("llm_model", "gpt-4o-mini")

    @property
    def is_configured(self):
        return bool(self.api_key)

    @staticmethod
    def _save(key, value):
        from voice_input.config import CONFIG_FILE, get_config
        import json
        cfg = get_config()
        cfg[key] = value
        CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
        CONFIG_FILE.write_text(json.dumps(cfg, indent=2, ensure_ascii=False))

    def refine(self, text, timeout=10):
        """Send text to LLM for conservative ASR error correction.
        Returns refined text, or raises on error.
        """
        base = self.api_base_url.rstrip("/")
        url = f"{base}/chat/completions"

        body = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": text},
            ],
            "temperature": 0.3,
        }

        req = urllib.request.Request(
            url,
            data=json.dumps(body).encode(),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
        )

        logger.info("LLM request: %s model=%s", url, self.model)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = json.loads(resp.read())
        except urllib.error.HTTPError as e:
            body_text = e.read().decode(errors="replace")
            logger.error("LLM HTTP %d: %s", e.code, body_text)
            raise
        except Exception:
            logger.exception("LLM request failed")
            raise

        try:
            content = data["choices"][0]["message"]["content"]
            refined = content.strip()
        except (KeyError, IndexError, TypeError) as e:
            logger.error("LLM response parse error: %s", e)
            raise ValueError("Invalid LLM response format") from e

        logger.info("Refined: %r -> %r", text, refined)
        return refined
