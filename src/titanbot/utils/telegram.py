# src/titanbot/utils/telegram.py # <-- Kommentar geändert
import requests
import logging

logger = logging.getLogger(__name__)

def send_message(bot_token, chat_id, message):
    if not bot_token or not chat_id:
        logger.warning("Telegram Bot-Token oder Chat-ID nicht konfiguriert.")
        return

    # Escape MarkdownV2 characters
    escape_chars = r'_*[]()~`>#+-=|{}.!'
    # Temporärer String zum Aufbau der Escaped-Nachricht
    escaped_message = ""
    for char in message:
        if char in escape_chars:
            escaped_message += f'\\{char}'
        else:
            escaped_message += char
    message = escaped_message # Überschreibe Original mit Escaped-Version

    api_url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    # Verwende MarkdownV2 für die Formatierung
    payload = {'chat_id': chat_id, 'text': message, 'parse_mode': 'MarkdownV2'}

    try:
        response = requests.post(api_url, data=payload, timeout=10)
        # attempt to capture API reply for debugging
        try:
            rsp_text = response.text
        except Exception:
            rsp_text = ''
        # write response JSON/text to project logs/telegram_api_debug.log (best-effort)
        try:
            from pathlib import Path
            root = Path(__file__).resolve().parents[2]
            log_path = root / 'logs' / 'telegram_api_debug.log'
            log_path.parent.mkdir(parents=True, exist_ok=True)
            with open(log_path, 'a', encoding='utf-8') as lf:
                lf.write(f"{datetime.now().isoformat()} - status={response.status_code} - text={rsp_text}\n")
        except Exception:
            pass
        response.raise_for_status()  # raises on 4xx/5xx
        # success
        return True
    except requests.exceptions.RequestException as e:
        logger.error(f"Netzwerkfehler beim Senden der Telegram-Nachricht: {e}")
        # log response text if available
        try:
            if hasattr(e, 'response') and e.response is not None:
                try:
                    from pathlib import Path
                    root = Path(__file__).resolve().parents[2]
                    log_path = root / 'logs' / 'telegram_api_debug.log'
                    log_path.parent.mkdir(parents=True, exist_ok=True)
                    with open(log_path, 'a', encoding='utf-8') as lf:
                        lf.write(f"{datetime.now().isoformat()} - exception - {e} - response={e.response.text if e.response is not None else ''}\n")
                except Exception:
                    pass
        except Exception:
            pass
        return False
    except Exception as e:
        logger.error(f"Allgemeiner Fehler beim Senden der Telegram-Nachricht: {e}")
        try:
            from pathlib import Path
            root = Path(__file__).resolve().parents[2]
            log_path = root / 'logs' / 'telegram_api_debug.log'
            log_path.parent.mkdir(parents=True, exist_ok=True)
            with open(log_path, 'a', encoding='utf-8') as lf:
                lf.write(f"{datetime.now().isoformat()} - unexpected error - {e}\n")
        except Exception:
            pass
        return False


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
            response.raise_for_status() # Prüft auf HTTP-Fehler
            if response.status_code != 200:
                 logger.error(f"Fehler beim Senden des Dokuments via Telegram (Status {response.status_code}): {response.text}")
            # Optional: Erfolgsmeldung
            # logger.debug(f"Dokument '{os.path.basename(file_path)}' erfolgreich an Chat {chat_id} gesendet.")

    except FileNotFoundError:
        logger.error(f"Zu sendende Datei nicht gefunden: {file_path}")
    except requests.exceptions.RequestException as e:
         logger.error(f"Netzwerkfehler beim Senden des Dokuments via Telegram: {e}")
    except Exception as e:
        logger.error(f"Allgemeiner Fehler beim Senden des Dokuments via Telegram: {e}")
