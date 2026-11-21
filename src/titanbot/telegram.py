"""Telegram-Meldungen (Text). secret.json: {"telegram": {"bot_token": "...", "chat_id": "..."}} wie bei den anderen Bots."""
from __future__ import annotations
import json, urllib.parse, urllib.request
from pathlib import Path


class TelegramHook:
    def __init__(self, secret_path: Path):
        self.secret_path = secret_path

    def _cfg(self):
        c = json.loads(self.secret_path.read_text(encoding='utf-8-sig'))['telegram']
        if isinstance(c, list) and len(c) == 1:
            c = c[0]
        token = c.get('token') or c.get('bot_token') or c.get('botToken')
        chat = c.get('chat_id') or c.get('chatId')
        if not token or not chat:
            raise ValueError('telegram in secret.json unvollständig')
        return token, chat

    def __call__(self, text: str):
        token, chat = self._cfg()
        data = urllib.parse.urlencode({'chat_id': chat, 'text': text[:4000]}).encode()
        with urllib.request.urlopen(urllib.request.Request('https://api.telegram.org/bot' + token + '/sendMessage', data=data), timeout=30) as r:
            if json.load(r).get('ok') is not True:
                raise ValueError('Telegram hat die Nachricht abgelehnt')
