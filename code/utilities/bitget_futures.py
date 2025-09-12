# code/utilities/bitget_futures.py

import ccxt
import logging
import time

logger = logging.getLogger('titan_bot')

class BitgetFutures:
    def __init__(self, account_config):
        self.api_key = account_config.get("apiKey")
        self.secret = account_config.get("secret")
        self.password = account_config.get("password")

        self.exchange = ccxt.bitget({
            'apiKey': self.api_key,
            'secret': self.secret,
            'password': self.password,
            'enableRateLimit': True,
            'options': {
                'defaultType': 'swap',  # Futures / Perpetuals
            },
        })

    # ===============================
    # Margin Mode
    # ===============================
    def set_margin_mode(self, symbol, margin_mode):
        """
        Setzt Margin Mode für ein Symbol (long & short).
        margin_mode: "crossed" oder "isolated"
        """
        try:
            # Long
            self.exchange.set_margin_mode(margin_mode, symbol, params={"holdSide": "long"})
            # Short
            self.exchange.set_margin_mode(margin_mode, symbol, params={"holdSide": "short"})
            logger.info(f"Margin-Modus für {symbol} auf '{margin_mode}' gesetzt (long & short).")

            # Kontrolle: aktuelle Positionen abfragen
            positions = self.exchange.fetch_positions([symbol])
            for pos in positions:
                logger.info(f"[Check] {symbol} | Side={pos.get('side')} | MarginMode={pos.get('marginMode')}")
        except Exception as e:
            logger.error(f"Fehler beim Setzen des Margin-Modus für {symbol}: {e}")

    # ===============================
    # Leverage
    # ===============================
    def set_leverage(self, symbol, leverage, margin_mode="isolated"):
        """
        Setzt Leverage für ein Symbol (long & short).
        """
        try:
            # Long
            self.exchange.set_leverage(leverage, symbol, params={"holdSide": "long", "marginMode": margin_mode})
            # Short
            self.exchange.set_leverage(leverage, symbol, params={"holdSide": "short", "marginMode": margin_mode})
            logger.info(f"Hebel für {symbol} auf {leverage}x gesetzt (long & short).")

            # Kontrolle: aktuelle Positionen abfragen
            positions = self.exchange.fetch_positions([symbol])
            for pos in positions:
                logger.info(f"[Check] {symbol} | Side={pos.get('side')} | Leverage={pos.get('leverage')}")
        except Exception as e:
            logger.error(f"Fehler beim Setzen des Hebels für {symbol}: {e}")

    # ===============================
    # Daten holen
    # ===============================
    def fetch_ticker(self, symbol):
        return self.exchange.fetch_ticker(symbol)

    def fetch_balance(self):
        return self.exchange.fetch_balance()

    def fetch_recent_ohlcv(self, symbol, timeframe, limit):
        data = self.exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
        return data

    def fetch_open_positions(self, symbol):
        return self.exchange.fetch_positions([symbol])

    def fetch_open_orders(self, symbol):
        return self.exchange.fetch_open_orders(symbol)

    # ===============================
    # Order-Funktionen
    # ===============================
    def place_limit_order(self, symbol, side, amount, price, leverage, margin_mode):
        try:
            order = self.exchange.create_order(
                symbol,
                type="limit",
                side=side,
                amount=amount,
                price=price,
                params={
                    "reduceOnly": False,
                    "leverage": leverage,
                    "marginMode": margin_mode,
                }
            )
            logger.info(f"Limit-Order platziert: {side} {amount} {symbol} @ {price}")
            return order
        except Exception as e:
            logger.error(f"Fehler beim Platzieren der Limit-Order: {e}")

    def place_trigger_market_order(self, symbol, side, amount, trigger_price, reduce=False):
        try:
            order = self.exchange.create_order(
                symbol,
                type="stop",
                side=side,
                amount=amount,
                params={
                    "triggerPrice": trigger_price,
                    "reduceOnly": reduce,
                }
            )
            logger.info(f"Trigger-Order platziert: {side} {amount} {symbol} @ Trigger={trigger_price}")
            return order
        except Exception as e:
            logger.error(f"Fehler beim Platzieren der Trigger-Order: {e}")

    def place_trailing_stop_order(self, symbol, side, amount, callback_rate, activation_price):
        try:
            order = self.exchange.create_order(
                symbol,
                type="trailing_stop",
                side=side,
                amount=amount,
                params={
                    "callbackRate": callback_rate,
                    "activationPrice": activation_price,
                    "reduceOnly": True
                }
            )
            logger.info(f"Trailing Stop platziert: {side} {amount} {symbol} | Activation={activation_price}, Callback={callback_rate}%")
            return order
        except Exception as e:
            logger.error(f"Fehler beim Platzieren des Trailing Stops: {e}")

    # ===============================
    # Order-Management
    # ===============================
    def cancel_all_orders(self, symbol):
        try:
            self.exchange.cancel_all_orders(symbol)
            logger.info(f"Alle offenen Orders für {symbol} gelöscht.")
        except Exception as e:
            logger.error(f"Fehler beim Löschen der Orders: {e}")

    def cancel_all_trigger_orders(self, symbol):
        try:
            # Manche Börsen trennen normale & Trigger-Orders → hier beide löschen
            self.exchange.cancel_all_orders(symbol, params={"stop": True})
            logger.info(f"Alle offenen Trigger-Orders für {symbol} gelöscht.")
        except Exception as e:
            logger.error(f"Fehler beim Löschen der Trigger-Orders: {e}")
