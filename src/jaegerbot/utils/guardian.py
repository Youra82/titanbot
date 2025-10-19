# src/jaegerbot/utils/guardian.py
import logging
from functools import wraps
from jaegerbot.utils.telegram import send_message

def guardian_decorator(func):
    """
    Ein Decorator, der eine Funktion umschließt, um alle unerwarteten
    Ausnahmen abzufangen, sie zu protokollieren und eine Telegram-Warnung zu senden,
    anstatt den Prozess abstürzen zu lassen.
    """
    @wraps(func)
    def wrapper(*args, **kwargs):
        # Die Logger- und Telegram-Konfiguration sind normalerweise in den args oder kwargs
        logger = None
        telegram_config = {}
        params = {}

        # Finde die relevanten Objekte in den Argumenten
        for arg in args:
            if isinstance(arg, logging.Logger):
                logger = arg
            if isinstance(arg, dict) and 'bot_token' in arg:
                telegram_config = arg
            if isinstance(arg, dict) and 'market' in arg:
                params = arg
        
        if not logger:
            # Fallback, falls kein Logger übergeben wird
            logger = logging.getLogger("guardian_fallback")
            logger.setLevel(logging.ERROR)
            if not logger.handlers:
                logger.addHandler(logging.StreamHandler())

        try:
            return func(*args, **kwargs)
        except Exception as e:
            symbol = params.get('market', {}).get('symbol', 'Unbekannt')
            timeframe = params.get('market', {}).get('timeframe', 'N/A')
            
            error_message = f"Ein kritischer Systemfehler ist im Guardian-Decorator für {symbol} ({timeframe}) aufgetreten."
            detailed_error = f"Fehlerdetails: {e.__class__.__name__}: {e}"
            
            logger.critical("!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!")
            logger.critical("!!! KRITISCHER SYSTEMFEHLER IM GUARDIAN !!!")
            logger.critical(f"!!! Strategie: {symbol} ({timeframe})")
            logger.critical(f"!!! Fehler: {e}", exc_info=True)
            logger.critical("!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!")

            # Sende eine Telegram-Nachricht
            try:
                telegram_message = f"🚨 *Kritischer Systemfehler* im Guardian-Decorator für *{symbol} ({timeframe})*."
                send_message(
                    telegram_config.get('bot_token'),
                    telegram_config.get('chat_id'),
                    telegram_message
                )
            except Exception as tel_e:
                logger.error(f"Konnte keine Telegram-Nachricht senden: {tel_e}")
    return wrapper
