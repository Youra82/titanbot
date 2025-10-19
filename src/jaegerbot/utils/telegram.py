# src/jaegerbot/utils/telegram.py
import requests
import logging

logger = logging.getLogger(__name__)

def send_message(bot_token, chat_id, message):
    if not bot_token or not chat_id:
        logger.warning("Telegram Bot-Token oder Chat-ID nicht konfiguriert.")
        return

    escape_chars = '_*[]()~`>#+-=|{}.!'
    for char in escape_chars:
        message = message.replace(char, f'\\{char}')

    api_url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = {'chat_id': chat_id, 'text': message, 'parse_mode': 'MarkdownV2'}

    try:
        response = requests.post(api_url, data=payload, timeout=10)
        if response.status_code != 200:
            logger.error(f"Fehler beim Senden der Telegram-Nachricht: {response.text}")
    except Exception as e:
        logger.error(f"Ausnahme beim Senden der Telegram-Nachricht: {e}")

def send_document(bot_token, chat_id, file_path, caption=""):
    """Sendet ein Dokument (z.B. eine CSV-Datei) an einen Telegram-Chat."""
    if not bot_token or not chat_id:
        logger.warning("Telegram Bot-Token oder Chat-ID nicht konfiguriert.")
        return

    api_url = f"https://api.telegram.org/bot{bot_token}/sendDocument"
    payload = {
        'chat_id': chat_id,
        'caption': caption
    }
    
    try:
        with open(file_path, 'rb') as doc:
            files = {'document': doc}
            response = requests.post(api_url, data=payload, files=files, timeout=30) # Timeout für Upload erhöht
            if response.status_code != 200:
                logger.error(f"Fehler beim Senden des Dokuments via Telegram: {response.text}")
    except FileNotFoundError:
        logger.error(f"Zu sendende Datei nicht gefunden: {file_path}")
    except Exception as e:
        logger.error(f"Ausnahme beim Senden des Dokuments via Telegram: {e}")
