# code/utilities/bitget_futures.py

import ccxt
import time
import pandas as pd
import logging
from typing import Any, Optional, Dict, List

logger = logging.getLogger(__name__)

class BitgetFutures():
    def __init__(self, api_setup: Optional[Dict[str, Any]] = None, demo_mode: bool = False) -> None:
        if api_setup is None:
            self.session = ccxt.bitget()
        else:
            api_setup.setdefault("options", {"defaultType": "future"})
            if demo_mode:
                api_setup["options"]["productType"] = "SUSDT-FUTURES"
            self.session = ccxt.bitget(api_setup)
            if demo_mode:
                self.session.set_sandbox_mode(True)
        self.markets = self.session.load_markets()
    
    def fetch_ticker(self, symbol: str) -> Dict[str, Any]:
        try:
            return self.session.fetch_ticker(symbol)
        except Exception as e:
            raise Exception(f"Failed to fetch ticker for {symbol}: {e}")

    def fetch_min_amount_tradable(self, symbol: str) -> float:
        try:
            return self.markets[symbol]['limits']['amount']['min']
        except Exception as e:
            raise Exception(f"Failed to fetch minimum amount tradable: {e}")    
        
    def amount_to_precision(self, symbol: str, amount: float) -> str:
        try:
            return self.session.amount_to_precision(symbol, amount)
        except Exception as e:
            raise Exception(f"Failed to convert amount {amount} {symbol} to precision", e)

    def price_to_precision(self, symbol: str, price: float) -> str:
        try:
            return self.session.price_to_precision(symbol, price)
        except Exception as e:
            raise Exception(f"Failed to convert price {price} to precision for {symbol}", e)

    def fetch_balance(self, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        if params is None:
            params = {}
        try:
            return self.session.fetch_balance(params)
        except Exception as e:
            raise Exception(f"Failed to fetch balance: {e}")

    def fetch_open_orders(self, symbol: str) -> List[Dict[str, Any]]:
        try:
            return self.session.fetch_open_orders(symbol)
        except Exception as e:
            raise Exception(f"Failed to fetch open orders: {e}")
            
    # <<< NEUE FUNKTION START >>>
    def fetch_my_trades(self, symbol: str, limit: int = 20) -> List[Dict[str, Any]]:
        """Holt die letzten geschlossenen Trades für ein Symbol."""
        try:
            return self.session.fetch_my_trades(symbol, limit=limit)
        except Exception as e:
            raise Exception(f"Failed to fetch my trades for {symbol}: {e}")
    # <<< NEUE FUNKTION ENDE >>>

    def fetch_open_trigger_orders(self, symbol: str) -> List[Dict[str, Any]]:
        try:
            return self.session.fetch_open_orders(symbol, params={'stop': True})
        except Exception as e:
            raise Exception(f"Failed to fetch open trigger orders: {e}")

    def cancel_order(self, id: str, symbol: str) -> Dict[str, Any]:
        try:
            return self.session.cancel_order(id, symbol)
        except Exception as e:
            raise Exception(f"Failed to cancel the {symbol} order {id}", e)

    def cancel_trigger_order(self, id: str, symbol: str) -> Dict[str, Any]:
        try:
            return self.session.cancel_order(id, symbol, params={'stop': True})
        except Exception as e:
            raise Exception(f"Failed to cancel the {symbol} trigger order {id}", e)

    def fetch_open_positions(self, symbol: str) -> List[Dict[str, Any]]:
        try:
            positions = self.session.fetch_positions([symbol], params={'productType': 'USDT-FUTURES', 'marginCoin': 'USDT'})
            real_positions = [p for p in positions if p.get('contracts') and float(p['contracts']) > 0]
            return real_positions
        except Exception as e:
            raise Exception(f"Failed to fetch open positions: {e}")

    def set_margin_mode(self, symbol: str, margin_mode: str = 'isolated') -> None:
        try:
            self.session.set_margin_mode(margin_mode, symbol, params={'productType': 'USDT-FUTURES', 'marginCoin': 'USDT'})
            logger.info(f"Margin-Modus für {symbol} auf '{margin_mode}' gesetzt.")
        except Exception as e:
            if 'repeat submit' in str(e):
                logger.info(f"Margin-Modus für {symbol} ist bereits auf '{margin_mode}' gesetzt.")
            else:
                raise Exception(f"Fehler beim Setzen des Margin-Modus: {e}")

    def set_leverage(self, symbol: str, leverage: int, margin_mode: str) -> None:
        try:
            if margin_mode == 'isolated':
                self.session.set_leverage(leverage, symbol, params={'holdSide': 'long'})
                self.session.set_leverage(leverage, symbol, params={'holdSide': 'short'})
            else:
                 self.session.set_leverage(leverage, symbol)
            logger.info(f"Hebel für {symbol} auf {leverage}x gesetzt.")
        except Exception as e:
            if 'repeat submit' in str(e):
                logger.info(f"Hebel für {symbol} ist bereits auf {leverage}x gesetzt.")
            else:
                raise Exception(f"Fehler beim Setzen des Hebels: {e}")

    def get_market_info(self, symbol: str) -> Dict[str, Any]:
        if symbol not in self.markets:
            self.markets = self.session.load_markets(True)
        
        market = self.markets.get(symbol)
        if not market:
            raise Exception(f"Markt-Informationen für {symbol} konnten nicht geladen werden.")
        
        return {
            'min_amount': market['limits']['amount']['min'],
            'amount_precision': market['precision']['amount']
        }

    def fetch_recent_ohlcv(self, symbol: str, timeframe: str, limit: int = 1000) -> pd.DataFrame:
        try:
            timeframe_in_ms = self.session.parse_timeframe(timeframe) * 1000
            since = self.session.milliseconds() - limit * timeframe_in_ms
            all_ohlcv = self.session.fetch_ohlcv(symbol, timeframe, since=since, limit=limit)
        except Exception as e:
            raise Exception(f"Failed to fetch OHLCV data for {symbol} in timeframe {timeframe}: {e}")

        df = pd.DataFrame(all_ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms', utc=True)
        df.set_index('timestamp', inplace=True)
        df.sort_index(inplace=True)
        return df

    def fetch_historical_ohlcv(self, symbol: str, timeframe: str, start_date_str: str, end_date_str: str) -> pd.DataFrame:
        from datetime import datetime, timezone
        start_ts = int(datetime.strptime(start_date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp() * 1000)
        end_ts = int(datetime.strptime(end_date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp() * 1000)
        all_ohlcv = []
        while start_ts < end_ts:
            try:
                ohlcv = self.session.fetch_ohlcv(symbol, timeframe, since=start_ts, limit=1000)
                if not ohlcv: break
                all_ohlcv.extend(ohlcv)
                last_timestamp = ohlcv[-1][0]
                start_ts = last_timestamp + self.session.parse_timeframe(timeframe) * 1000
            except Exception as e:
                raise Exception(f"Failed to fetch historical OHLCV data for {symbol}: {e}")
        
        if not all_ohlcv: return pd.DataFrame()
        df = pd.DataFrame(all_ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms', utc=True)
        df.set_index('timestamp', inplace=True)
        df = df[~df.index.duplicated(keep='first')]
        df.sort_index(inplace=True)
        return df
    
    def place_limit_order(self, symbol: str, side: str, amount: float, price: float, leverage: int, margin_mode: str, reduce: bool = False) -> Dict[str, Any]:
        try:
            params = {
                'reduceOnly': reduce,
                'marginMode': margin_mode,
                'leverage': leverage,
            }
            amount_str = self.session.amount_to_precision(symbol, amount)
            price_str = self.session.price_to_precision(symbol, price)
            
            response = self.session.create_order(symbol, 'limit', side, float(amount_str), float(price_str), params=params)
            return response
        except Exception as e:
            raise Exception(f"Failed to place limit order of {amount} {symbol} at price {price}: {e}")

    def place_trigger_market_order(self, symbol: str, side: str, amount: float, trigger_price: float, reduce: bool = False) -> Optional[Dict[str, Any]]:
        try:
            amount_str = self.session.amount_to_precision(symbol, amount)
            trigger_price_str = self.session.price_to_precision(symbol, trigger_price)
            params = {
                'reduceOnly': reduce,
                'stopPrice': trigger_price_str,
            }
            return self.session.create_order(symbol, 'market', side, float(amount_str), price=None, params=params)
        except Exception as err:
            raise err

