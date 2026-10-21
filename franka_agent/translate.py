"""Multilingual front end: NVIDIA Riva Translate around an English-only planner.

The user's command is translated to English before planning, and the planner's
final reply is translated back. The planner, its tools and the evals stay in
English, so adding a language never touches the planning prompt.

Riva Translate 4B Instruct runs on the NVIDIA API Catalog (build.nvidia.com) behind
the same OpenAI-compatible endpoint as Nemotron. Its fine-tuning prompt is a
system message naming the direction, e.g. "en-zh-cn", followed by the text.
"""

from __future__ import annotations

import os

TRANSLATE_MODEL = "nvidia/riva-translate-4b-instruct-v2"
BASE_URL = "https://integrate.api.nvidia.com/v1"

# Languages Riva Translate pairs with English (codes as listed on the model card).
LANGUAGES = {
    "ar": "Arabic", "bg": "Bulgarian", "cs": "Czech", "da": "Danish", "de": "German",
    "el": "Greek", "es-ES": "European Spanish", "es-US": "LATAM Spanish", "et": "Estonian",
    "fi": "Finnish", "fr": "French", "hi": "Hindi", "hr": "Croatian", "hu": "Hungarian",
    "id": "Indonesian", "it": "Italian", "ja": "Japanese", "ko": "Korean", "lt": "Lithuanian",
    "lv": "Latvian", "nl": "Dutch", "no": "Norwegian", "pl": "Polish",
    "pt-BR": "Brazilian Portuguese", "pt-PT": "European Portuguese", "ro": "Romanian",
    "ru": "Russian", "sk": "Slovak", "sl": "Slovenian", "sv": "Swedish", "th": "Thai",
    "tr": "Turkish", "uk": "Ukrainian", "vi": "Vietnamese", "zh-CN": "Simplified Chinese",
    "zh-TW": "Traditional Chinese",
}


class Translator:
    """Translates between English and one user language."""

    def __init__(self, lang: str, client=None, model: str = TRANSLATE_MODEL):
        if lang not in LANGUAGES:
            raise SystemExit(f"unsupported language {lang!r}; choose from: {', '.join(sorted(LANGUAGES))}")
        if client is None:
            key = os.environ.get("NVIDIA_API_KEY", "")
            if not key:
                raise SystemExit("--lang needs NVIDIA_API_KEY set (free key at build.nvidia.com)")
            from openai import OpenAI

            client = OpenAI(base_url=BASE_URL, api_key=key)
        self.client, self.model, self.lang = client, model, lang

    def _translate(self, text: str, src: str, tgt: str) -> str:
        if not text.strip():
            return text
        response = self.client.chat.completions.create(
            model=self.model,
            messages=[{"role": "system", "content": f"{src}-{tgt}".lower()}, {"role": "user", "content": text}],
            temperature=0.0,
            max_tokens=1024,
        )
        return (response.choices[0].message.content or "").strip()

    def to_english(self, text: str) -> str:
        return self._translate(text, self.lang, "en")

    def from_english(self, text: str) -> str:
        return self._translate(text, "en", self.lang)
