import ccxt
import logging
import time

logger = logging.getLogger("titan_bot")

class BitgetFutures:
    def __init__(self, account_config):
        self.exchange = ccxt.bitget({
            "apiKey": account_config["apiKey"],
            "secret": account_config["secret"],
            "password": account_config["password"],
            "enableRateLimit": True,
            "options": {
                "defaultType": "swap",   # USDT-M Futures
            }
        })

    # =====================================================
    # Margin Mode
    # =====================================================
    def set_margin_mode(self, symbol, margin_mode):
        try:
            # Beide Richtungen explizit setzen
            self.exchange.set_margin_mode(margin_mode, symbol, params={"holdSide": "long"})
            self.exchange.set_margin_mode(margin_mode, symbol, params={"holdSide": "short"})
            logger.info(f"Margin-Modus für {symbol} auf '{margin_mode}' (long & short) gesetzt.")
        except Exception as e:
            logger.error(f"Fehler beim Setzen des Margin-Modus für {symbol}: {e}")

    # =====================================================
    # Leverage
    # =====================================================
    def set_leverage(self, symbol, leverage, margin_mode):
        try:
            # Beide Richtungen explizit setzen
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
    # Order Management
    # =====================================================
    def cancel_all_orders(self, symbol):
        try:
            self.exchange.cancel_all_orders(symbol)
            logger.info(f"Alle offenen Orders für {symbol} wurden gelöscht.")
        except Exception as e:
            logger.error(f"Fehler beim Löschen der Orders für {symbol}: {e}")

    def cancel_all_trigger_orders(self, symbol):
        try:
            # Bitget-spezifisch: Alle Plan Orders löschen
            self.exchange.private_mix_post_plan_cancelallorders({"symbol": symbol.replace("/", "")})
            logger.info(f"Alle offenen Trigger-Orders (SL/TP) für {symbol} wurden gelöscht.")
        except Exception as e:
            logger.error(f"Fehler beim Löschen der Trigger-Orders für {symbol}: {e}")

    # =====================================================
    # Entry-Order
    # =====================================================
    def place_limit_order(self, symbol, side, amount, price, leverage, margin_mode):
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
    # Trigger Orders (TP/SL)
    # =====================================================
    def place_trigger_market_order(self, symbol, side, amount, trigger_price, reduce=True):
        """
        Legt eine Trigger-Order (Plan Order) bei Bitget an
        """
        try:
            market = self.exchange.market(symbol)
            payload = {
                "symbol": market["id"],  # z.B. "XRPUSDT"
                "marginCoin": "USDT",
                "size": str(amount),
                "side": side,
                "orderType": "market",
                "reduceOnly": reduce,
                "triggerType": "fill_price",
                "triggerPrice": str(trigger_price),
                "executePrice": "0",  # 0 = Market
                "planType": "normal_plan",
            }
            response = self.exchange.private_mix_post_plan_placeplanorder(payload)
            logger.info(f"Trigger-Order gesetzt: {side.upper()} {amount} @ Trigger={trigger_price}")
            return response
        except Exception as e:
            logger.error(f"Fehler beim Platzieren der Trigger-Order für {symbol}: {e}")

    # =====================================================
    # Trailing Stop
    # =====================================================
    def place_trailing_stop_order(self, symbol, side, amount, callback_rate, trigger_price):
        try:
            market = self.exchange.market(symbol)
            payload = {
                "symbol": market["id"],
                "marginCoin": "USDT",
                "size": str(amount),
                "side": side,
                "orderType": "market",
                "triggerType": "fill_price",
                "triggerPrice": str(trigger_price),
                "executePrice": "0",
                "planType": "track_plan",
                "callbackRatio": str(callback_rate),
                "reduceOnly": True
            }
            response = self.exchange.private_mix_post_plan_placeplanorder(payload)
            logger.info(f"Trailing Stop gesetzt: {side.upper()} {amount} @ Trigger={trigger_price} mit Callback={callback_rate}%")
            return response
        except Exception as e:
            logger.error(f"Fehler beim Platzieren des Trailing Stops für {symbol}: {e}")
