"""Telegram delivery via the Bot API (plain requests - no async needed)."""

from __future__ import annotations

import logging
import time

import requests

log = logging.getLogger(__name__)

API_ROOT = "https://api.telegram.org"
MAX_MESSAGE_CHARS = 4096


class TelegramError(RuntimeError):
    pass


class TelegramNotifier:
    def __init__(self, token: str, chat_id: str, timeout: float = 20.0, max_retries: int = 3) -> None:
        if not token:
            raise TelegramError("Missing TELEGRAM_BOT_TOKEN")
        if not chat_id:
            raise TelegramError("Missing TELEGRAM_CHAT_ID")
        self.token = token
        self.chat_id = str(chat_id)
        self.timeout = timeout
        self.max_retries = max_retries
        self.session = requests.Session()

    # ------------------------------------------------------------------ #
    def _call(self, method: str, payload: dict) -> dict:
        url = f"{API_ROOT}/bot{self.token}/{method}"
        last_error: Exception | None = None

        for attempt in range(1, self.max_retries + 1):
            try:
                response = self.session.post(url, json=payload, timeout=self.timeout)
            except requests.RequestException as exc:
                last_error = exc
                log.warning("Telegram %s attempt %d failed: %s", method, attempt, exc)
                time.sleep(min(2 ** attempt, 15))
                continue

            if response.status_code == 429:
                retry_after = 5
                try:
                    retry_after = int(response.json().get("parameters", {}).get("retry_after", 5))
                except Exception:  # noqa: BLE001
                    pass
                log.warning("Telegram rate limited; sleeping %ss", retry_after)
                time.sleep(retry_after + 1)
                continue

            try:
                body = response.json()
            except ValueError as exc:
                raise TelegramError(f"Telegram returned non-JSON ({response.status_code})") from exc

            if body.get("ok"):
                return body.get("result", {})

            description = body.get("description", "unknown error")
            if response.status_code >= 500:
                last_error = TelegramError(description)
                time.sleep(min(2 ** attempt, 15))
                continue
            raise TelegramError(f"Telegram API error ({response.status_code}): {description}")

        raise TelegramError(f"Telegram {method} failed after {self.max_retries} attempts: {last_error}")

    # ------------------------------------------------------------------ #
    def send_message(self, text: str, parse_mode: str = "HTML", disable_preview: bool = True) -> list[dict]:
        """Send text, splitting on line boundaries if it exceeds Telegram's limit."""
        results = []
        for chunk in _split_message(text):
            payload = {
                "chat_id": self.chat_id,
                "text": chunk,
                "parse_mode": parse_mode,
                "disable_web_page_preview": disable_preview,
            }
            try:
                results.append(self._call("sendMessage", payload))
            except TelegramError as exc:
                # HTML parse failures shouldn't lose the message - retry as plain text.
                if "can't parse entities" in str(exc).lower():
                    log.warning("HTML parse failed; resending as plain text")
                    payload.pop("parse_mode")
                    payload["text"] = _strip_html(chunk)
                    results.append(self._call("sendMessage", payload))
                else:
                    raise
        log.info("Sent %d Telegram message(s) to chat %s", len(results), self.chat_id)
        return results

    def get_me(self) -> dict:
        return self._call("getMe", {})


def _split_message(text: str, limit: int = MAX_MESSAGE_CHARS) -> list[str]:
    if len(text) <= limit:
        return [text]
    chunks: list[str] = []
    current: list[str] = []
    size = 0
    for line in text.split("\n"):
        line_size = len(line) + 1
        if size + line_size > limit and current:
            chunks.append("\n".join(current))
            current, size = [], 0
        current.append(line)
        size += line_size
    if current:
        chunks.append("\n".join(current))
    return chunks


def _strip_html(text: str) -> str:
    import re
    from html import unescape

    return unescape(re.sub(r"<[^>]+>", "", text))
