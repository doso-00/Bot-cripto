"""Client Binance di sola lettura (endpoint pubblici) costruito su ccxt.

Espone solo i metodi che servono al bot, così l'insieme di chiamate verso l'exchange
resta esplicito e verificabile. Non usa chiavi API.

Endpoint usati (binance-spot-api-docs, rest-api.md):
- GET /api/v3/klines        peso 2, massimo 1000 candele per richiesta
- GET /api/v3/exchangeInfo  peso 20
Il rispetto dei rate limit è delegato a ccxt (`enableRateLimit`); in più, su errori di
rete o 429 si ritenta con backoff esponenziale e poi ci si ferma.
"""

import json
import logging
import time
from typing import Any

import ccxt

from bot.data.klines import to_binance_symbol

log = logging.getLogger(__name__)

KLINES_MAX_LIMIT = 1000
RETRY_DELAYS_S = (2, 4, 8, 16)


class BinancePublicClient:
    def __init__(self, exchange: Any | None = None):
        self._ex = exchange or ccxt.binance({"enableRateLimit": True, "options": {"defaultType": "spot"}})

    def fetch_klines(
        self, symbol: str, timeframe: str, start_ms: int, limit: int = KLINES_MAX_LIMIT
    ) -> list[list]:
        """Klines grezze (12 campi) a partire da `start_ms`, in ordine cronologico.

        Attenzione: l'ultima riga può essere la candela ancora aperta.
        """
        params = {
            "symbol": to_binance_symbol(symbol),
            "interval": timeframe,
            "startTime": start_ms,
            "limit": min(limit, KLINES_MAX_LIMIT),
        }
        return self._call(self._ex.public_get_klines, params)

    def fetch_exchange_info(self, symbols: list[str]) -> dict:
        params = {"symbols": json.dumps([to_binance_symbol(s) for s in symbols], separators=(",", ":"))}
        return self._call(self._ex.public_get_exchangeinfo, params)

    @staticmethod
    def _call(method, params: dict):
        for attempt, delay in enumerate((*RETRY_DELAYS_S, None), start=1):
            try:
                return method(params)
            except ccxt.NetworkError as exc:  # include RateLimitExceeded e DDoSProtection
                if delay is None:
                    raise
                log.warning("errore di rete Binance (tentativo %d): %s; riprovo tra %ss", attempt, exc, delay)
                time.sleep(delay)
        raise AssertionError("non raggiungibile")
