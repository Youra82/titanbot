import ccxt
import logging

logger = logging.getLogger("titan_bot")

class BitgetFutures:
    def __init__(self, account_config):
        self.exchange = ccxt.bitget({
            "apiKey": account_config["apiKey"],
            "secret": account_config["secret"],
            "password": account_config["password"],
            "enableRateLimit": True,
            "options": {
                "defaultType": "swap",  # USDT-M Futures
            }
        })
        self.exchange.load_markets()

    # =====================================================
    # Margin Mode
    # =====================================================
    def set_margin_mode(self, symbol, margin_mode):
        try:
            self.exchange.set_margin_mode(margin_mode, symbol, params={"holdSide": "long"})
            self.exchange.set_margin_mode(margin_mode, symbol, params={"holdSide": "short"})
            logger.info(f"Margin-Modus für {symbol} auf '{margin_mode}' (long & short) gesetzt.")
        except Exception as e:
            logger.error(f"Fehler beim Setzen des Margin-Modus für {symbol}: {e}")

    # =====================================================
    # Leverage
    # =====================================================
    def set_leverage(self, symbol, leverage):
        try:
            self.exchange.set_leverage(leverage, symbol, params={"holdSide": "long"})
            self.exchange.set_leverage(leverage, symbol, params={"holdSide": "short"})
            logger.info(f"Leverage für {symbol} auf {leverage}x (long & short) gesetzt.")
        except Exception as e:
            logger.error(f"Fehler beim Setzen des Leverage für {symbol}: {e}")

    # =====================================================
    # Datenabfragen
    # =====================================================
    def fetch_balance(self):
        return self.exchange.fetch_balance()

    def fetch_ticker(self, symbol):
        return self.exchange.fetch_ticker(symbol)

    def fetch_recent_ohlcv(self, symbol, timeframe, limit=500):
        return self.exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)

    def fetch_open_positions(self, symbol):
        return self.exchange.fetch_positions([symbol])

    def fetch_open_orders(self, symbol):
        return self.exchange.fetch_open_orders(symbol)

    # =====================================================
    # Orders
    # =====================================================
    def cancel_all_orders(self, symbol):
        try:
            self.exchange.cancel_all_orders(symbol)
            logger.info(f"Alle offenen Orders für {symbol} wurden gelöscht.")
        except Exception as e:
            logger.warning(f"Konnte keine Orders für {symbol} löschen: {e}")

    # Entry-Order
    def place_limit_order(self, symbol, side, amount, price):
        try:
            order = self.exchange.create_order(
                symbol=symbol,
                type="limit",
                side=side,
                amount=amount,
                price=price,
                params={"reduceOnly": False}
            )
            logger.info(f"Limit-Order gesetzt: {side.upper()} {amount} @ {price}")
            return order
        except Exception as e:
            logger.error(f"Fehler beim Platzieren der Limit-Order für {symbol}: {e}")

    # =====================================================
    # Stop / Take Profit Orders (SL/TP)
    # =====================================================
    def place_stop_loss_order(self, symbol, side, amount, stop_price):
        """
        Stop-Loss Order platzieren
        """
        try:
            close_side = "sell" if side == "buy" else "buy"
            order = self.exchange.create_order(
                symbol=symbol,
                type="stop",  # ccxt stop order
                side=close_side,
                amount=amount,
                price=None,
                params={"stopPrice": stop_price, "reduceOnly": True}
            )
            logger.info(f"Stop-Loss gesetzt: {close_side.upper()} {amount} @ {stop_price}")
            return order
        except Exception as e:
            logger.error(f"Fehler beim Platzieren der Stop-Loss-Order für {symbol}: {e}")

    def place_take_profit_order(self, symbol, side, amount, take_price):
        """
        Take-Profit Order platzieren
        """
        try:
            close_side = "sell" if side == "buy" else "buy"
            order = self.exchange.create_order(
                symbol=symbol,
                type="takeProfit",  # ccxt takeProfit order
                side=close_side,
                amount=amount,
                price=None,
                params={"stopPrice": take_price, "reduceOnly": True}
            )
            logger.info(f"Take-Profit gesetzt: {close_side.upper()} {amount} @ {take_price}")
            return order
        except Exception as e:
            logger.error(f"Fehler beim Platzieren der Take-Profit-Order für {symbol}: {e}")
