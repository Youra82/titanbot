# code/utilities/bitget_futures.py
import ccxt
import pandas as pd
import logging
from typing import Any, Optional, Dict, List
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

class BitgetFutures():
    def __init__(self, api_setup: Optional[Dict[str, Any]] = None, demo_mode: bool = False) -> None:
        api_setup = api_setup or {}
        api_setup.setdefault("options", {"defaultType": "swap"})
        if demo_mode:
            api_setup["options"]["productType"] = "SUSDT-FUTURES"
            self.session = ccxt.bitget(api_setup)
            self.session.set_sandbox_mode(True)
        else:
            self.session = ccxt.bitget(api_setup)
        self.markets = self.session.load_markets()
     
    def get_market_info(self, symbol: str) -> Dict[str, Any]:
        if symbol not in self.markets:
            self.markets = self.session.load_markets(True)
        return self.markets.get(symbol, {})

    def fetch_ticker(self, symbol: str) -> Dict[str, Any]:
        try:
            return self.session.fetch_ticker(symbol)
        except Exception as e:
            raise Exception(f"Failed to fetch ticker for {symbol}: {e}")
         
    def fetch_balance(self) -> Dict[str, Any]:
        try:
            return self.session.fetch_balance()
        except Exception as e:
            raise Exception(f"Failed to fetch balance: {e}")

    def fetch_open_orders(self, symbol: str) -> List[Dict[str, Any]]:
        try:
            return self.session.fetch_open_orders(symbol)
        except Exception as e:
            raise Exception(f"Failed to fetch open orders: {e}")

    def fetch_open_positions(self, symbol: str) -> List[Dict[str, Any]]:
        try:
            positions = self.session.fetch_positions([symbol])
            return [p for p in positions if p.get('contracts') and float(p['contracts']) > 0]
        except Exception as e:
            raise Exception(f"Failed to fetch open positions: {e}")
            
    def fetch_recent_ohlcv(self, symbol: str, timeframe: str, limit: int = 500) -> pd.DataFrame:
        try:
            timeframe_in_ms = self.session.parse_timeframe(timeframe) * 1000
            since = self.session.milliseconds() - limit * timeframe_in_ms
            ohlcv = self.session.fetch_ohlcv(symbol, timeframe, since=since, limit=limit)
            df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
            df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms', utc=True)
            df.set_index('timestamp', inplace=True)
            return df.sort_index()
        except Exception as e:
            raise Exception(f"Failed to fetch ohlcv data for {symbol}: {e}")

    # === HIER IST DIE WIEDERHERGESTELLTE FUNKTION ===
    def fetch_historical_ohlcv(self, symbol: str, timeframe: str, start_date_str: str, end_date_str: str) -> pd.DataFrame:
        start_ts = int(datetime.strptime(start_date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp() * 1000)
        end_ts = int(datetime.strptime(end_date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp() * 1000)
        all_ohlcv = []
        
        while start_ts < end_ts:
            try:
                ohlcv = self.session.fetch_ohlcv(symbol, timeframe, since=start_ts, limit=1000)
                if not ohlcv:
                    break
                all_ohlcv.extend(ohlcv)
                last_timestamp = ohlcv[-1][0]
                start_ts = last_timestamp + self.session.parse_timeframe(timeframe) * 1000
            except Exception as e:
                raise Exception(f"Failed to fetch historical OHLCV data for {symbol}: {e}")
        
        if not all_ohlcv:
            return pd.DataFrame()

        df = pd.DataFrame(all_ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms', utc=True)
        df.set_index('timestamp', inplace=True)
        df = df[~df.index.duplicated(keep='first')] # Doppelte Einträge entfernen
        df.sort_index(inplace=True)
        return df

    def set_margin_mode(self, symbol: str, margin_mode: str):
        try:
            return self.session.set_margin_mode(margin_mode.lower(), symbol)
        except Exception as e:
            if 'Margin mode is the same' in str(e):
                logger.info(f"Margin-Modus für {symbol} ist bereits '{margin_mode}'.")
            else:
                logger.warning(f"Fehler beim Voreinstellen des Margin-Modus (wird bei Order erneut versucht): {e}")

    def set_leverage(self, symbol: str, leverage: float, margin_mode: str):
        try:
            params = {}
            if margin_mode.lower() == 'isolated':
                params['holdSide'] = 'long'
                self.session.set_leverage(leverage, symbol, params)
                params['holdSide'] = 'short'
                self.session.set_leverage(leverage, symbol, params)
            else: # cross
                self.session.set_leverage(leverage, symbol)
            logger.info(f"Hebel für {symbol} auf {leverage}x gesetzt.")
        except Exception as e:
            if 'Leverage not changed' in str(e):
                logger.info(f"Hebel für {symbol} ist bereits auf {leverage}x gesetzt.")
            else:
                logger.warning(f"Fehler beim Voreinstellen des Hebels (wird bei Order erneut versucht): {e}")
    
    def cancel_all_orders(self, symbol: str):
        try:
            self.session.cancel_all_orders(symbol)
        except Exception as e:
            logger.warning(f"Konnte nicht alle Limit-Orders stornieren: {e}")
    
    def cancel_all_trigger_orders(self, symbol: str):
        try:
            self.session.cancel_all_orders(symbol, params={'planType': 'normal_plan'})
            self.session.cancel_all_orders(symbol, params={'planType': 'track_plan'})
        except Exception as e:
            logger.warning(f"Konnte nicht alle Trigger-Orders stornieren: {e}")

    def cancel_order(self, id: str, symbol: str) -> Dict[str, Any]:
        try:
            return self.session.cancel_order(id, symbol)
        except Exception as e:
            raise Exception(f"Failed to cancel the {symbol} order {id}", e)

    def create_market_order(self, symbol: str, side: str, amount: float, leverage: int, margin_mode: str, params: dict = None) -> Dict[str, Any]:
        try:
            order_params = {
                'leverage': leverage,
                'marginMode': margin_mode.lower()
            }
            if params:
                order_params.update(params)
            return self.session.create_order(symbol, 'market', side, amount, None, order_params)
        except Exception as e:
            raise Exception(f"Failed to create market order: {e}")
    
    def place_limit_order(self, symbol: str, side: str, amount: float, price: float, leverage: int, margin_mode: str, params: dict = None) -> Dict[str, Any]:
        try:
            order_params = {
                'leverage': leverage,
                'marginMode': margin_mode.lower()
            }
            if params:
                order_params.update(params)
            return self.session.create_order(symbol, 'limit', side, amount, price, order_params)
        except Exception as e:
            raise Exception(f"Failed to place limit order: {e}")

    def place_trigger_market_order(self, symbol: str, side: str, amount: float, trigger_price: float, params: dict = None) -> Dict[str, Any]:
        try:
            order_params = {'stopPrice': trigger_price}
            if params:
                order_params.update(params)
            return self.session.create_order(symbol, 'market', side, amount, None, order_params)
        except Exception as e:
            raise Exception(f"Failed to place trigger market order: {e}")

    def place_trailing_stop_order(self, symbol: str, side: str, amount: float, callback_rate: float, activation_price: float, params: dict = None) -> Dict[str, Any]:
        try:
            order_params = {
                'planType': 'track_plan', 'triggerPrice': activation_price,
                'callbackRate': str(callback_rate / 100),
            }
            if params:
                order_params.update(params)
            return self.session.create_order(symbol, 'market', side, amount, None, params=order_params)
        except Exception as e:
            raise Exception(f"Failed to place trailing stop order: {e}")
