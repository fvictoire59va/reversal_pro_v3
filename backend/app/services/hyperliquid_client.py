"""
Hyperliquid DEX client — manages orders and positions on https://app.hyperliquid.xyz

Supports both paper trading (simulation) and live trading modes.
Uses exponential backoff retry for all API calls.
"""

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Optional, Dict, Any, List

import httpx

from ..config import get_settings

logger = logging.getLogger(__name__)

# ── Constants ────────────────────────────────────────────────
HYPERLIQUID_API_URL = "https://api.hyperliquid.xyz"
HYPERLIQUID_INFO_URL = f"{HYPERLIQUID_API_URL}/info"
HYPERLIQUID_EXCHANGE_URL = f"{HYPERLIQUID_API_URL}/exchange"

MAX_RETRIES = 5
BASE_DELAY = 1.0  # seconds
MAX_DELAY = 30.0  # seconds

# EUR/USDT rate cache (TTL-based)
_eur_usdt_rate_cache: Optional[float] = None
_eur_usdt_rate_ts: float = 0.0
_EUR_USDT_CACHE_TTL = 60.0  # seconds


@dataclass
class OrderResult:
    """Result of an order execution."""
    success: bool
    order_id: Optional[str] = None
    filled_price: Optional[float] = None
    quantity: Optional[float] = None
    error: Optional[str] = None
    is_paper: bool = False


@dataclass
class PositionInfo:
    """Position information from exchange."""
    symbol: str
    side: str
    size: float
    entry_price: float
    unrealized_pnl: float
    leverage: int


async def _retry_with_backoff(func, *args, **kwargs):
    """Execute an async function with exponential backoff retry."""
    last_error = None
    for attempt in range(MAX_RETRIES):
        try:
            return await func(*args, **kwargs)
        except Exception as e:
            last_error = e
            if attempt < MAX_RETRIES - 1:
                delay = min(BASE_DELAY * (2 ** attempt), MAX_DELAY)
                logger.warning(
                    f"Attempt {attempt + 1}/{MAX_RETRIES} failed: {e}. "
                    f"Retrying in {delay:.1f}s..."
                )
                await asyncio.sleep(delay)
            else:
                logger.error(f"All {MAX_RETRIES} attempts failed: {e}")
    raise last_error


class HyperliquidClient:
    """
    Client for Hyperliquid DEX.
    
    In paper mode: simulates orders locally without hitting the exchange.
    In live mode: sends real orders via the Hyperliquid API.
    """

    def __init__(self):
        self._http_client: Optional[httpx.AsyncClient] = None
        # Paper trading state
        self._paper_positions: Dict[str, Dict] = {}
        self._paper_order_counter = 0
        # Dynamic asset mapping cache (populated from /info meta endpoint)
        self._asset_map_cache: Dict[str, int] = {}
        self._size_decimals_cache: Dict[str, int] = {}

    async def _get_client(self) -> httpx.AsyncClient:
        if self._http_client is None or self._http_client.is_closed:
            self._http_client = httpx.AsyncClient(
                timeout=30.0,
                headers={"Content-Type": "application/json"},
            )
        return self._http_client

    async def close(self):
        if self._http_client and not self._http_client.is_closed:
            await self._http_client.aclose()

    # ── EUR → USDT conversion ────────────────────────────────
    async def get_eur_usdt_rate(self) -> float:
        """Get current EUR/USDT exchange rate. Cached for 60s. Falls back to fixed rate."""
        global _eur_usdt_rate_cache, _eur_usdt_rate_ts

        now = time.monotonic()
        if _eur_usdt_rate_cache is not None and (now - _eur_usdt_rate_ts) < _EUR_USDT_CACHE_TTL:
            return _eur_usdt_rate_cache

        try:
            client = await self._get_client()
            resp = await client.get(
                "https://api.coingecko.com/api/v3/simple/price",
                params={"ids": "tether", "vs_currencies": "eur"},
            )
            if resp.status_code == 200:
                data = resp.json()
                eur_per_usdt = data.get("tether", {}).get("eur", 0.92)
                rate = 1.0 / eur_per_usdt if eur_per_usdt > 0 else 1.09
                logger.info(f"EUR/USDT rate: {rate:.4f} (cached for {_EUR_USDT_CACHE_TTL}s)")
                _eur_usdt_rate_cache = rate
                _eur_usdt_rate_ts = now
                return rate
        except Exception as e:
            logger.warning(f"Failed to fetch EUR/USDT rate: {e}, using {'cached' if _eur_usdt_rate_cache else 'fallback'}")
            if _eur_usdt_rate_cache is not None:
                return _eur_usdt_rate_cache
        return 1.09  # Fallback fixed rate

    async def convert_eur_to_usdt(self, eur_amount: float) -> float:
        """Convert EUR amount to USDT."""
        rate = await self.get_eur_usdt_rate()
        return eur_amount * rate

    async def convert_usdt_to_eur(self, usdt_amount: float) -> float:
        """Convert USDT amount to EUR."""
        rate = await self.get_eur_usdt_rate()
        return usdt_amount / rate

    # ── Market data ──────────────────────────────────────────
    async def get_mid_price(self, symbol: str) -> Optional[float]:
        """Get current mid price for a symbol from Hyperliquid."""
        try:
            async def _fetch():
                client = await self._get_client()
                # Hyperliquid uses coin names like "BTC", "ETH"
                coin = symbol.split("/")[0] if "/" in symbol else symbol.split("USDT")[0]
                resp = await client.post(
                    HYPERLIQUID_INFO_URL,
                    json={"type": "allMids"},
                )
                resp.raise_for_status()
                data = resp.json()
                if coin in data:
                    return float(data[coin])
                return None
            return await _retry_with_backoff(_fetch)
        except Exception as e:
            logger.error(f"Failed to get mid price for {symbol}: {e}")
            return None

    # ── Paper trading methods ────────────────────────────────
    async def paper_market_open(
        self, symbol: str, side: str, usdt_amount: float, current_price: float
    ) -> OrderResult:
        """Simulate opening a market position (paper trading)."""
        self._paper_order_counter += 1
        order_id = f"PAPER-{self._paper_order_counter}-{int(time.time())}"
        quantity = usdt_amount / current_price

        logger.info(
            f"[PAPER] Opening {side} position: {symbol} "
            f"qty={quantity:.6f} @ {current_price:.2f} "
            f"(${usdt_amount:.2f})"
        )

        return OrderResult(
            success=True,
            order_id=order_id,
            filled_price=current_price,
            quantity=quantity,
            is_paper=True,
        )

    async def paper_market_close(
        self, symbol: str, side: str, quantity: float, current_price: float
    ) -> OrderResult:
        """Simulate closing a market position (paper trading)."""
        self._paper_order_counter += 1
        order_id = f"PAPER-CLOSE-{self._paper_order_counter}-{int(time.time())}"

        logger.info(
            f"[PAPER] Closing {side} position: {symbol} "
            f"qty={quantity:.6f} @ {current_price:.2f}"
        )

        return OrderResult(
            success=True,
            order_id=order_id,
            filled_price=current_price,
            quantity=quantity,
            is_paper=True,
        )

    # ── Live trading methods (Hyperliquid API) ───────────────
    async def live_market_open(
        self,
        symbol: str,
        side: str,
        usdt_amount: float,
        current_price: float,
        wallet_address: str,
        api_secret: str,
    ) -> OrderResult:
        """
        Open a real market position on Hyperliquid.
        
        Note: Full implementation requires the hyperliquid-python-sdk.
        This is a REST API placeholder that can be extended.
        """
        try:
            async def _execute():
                coin = symbol.split("/")[0] if "/" in symbol else symbol.split("USDT")[0]
                quantity = usdt_amount / current_price

                # Round quantity to appropriate precision
                # Hyperliquid has specific size decimals per asset
                sz_decimals = await self._get_size_decimals(coin)
                quantity = round(quantity, sz_decimals)

                is_buy = (side == "LONG")

                logger.info(
                    f"[LIVE] Opening {side} position: {coin} "
                    f"qty={quantity} @ market (~{current_price:.2f})"
                )

                # NOTE: For production, integrate hyperliquid-python-sdk:
                #   from hyperliquid.exchange import Exchange
                #   from hyperliquid.utils import constants
                #   exchange = Exchange(wallet, constants.MAINNET_API_URL)
                #   result = exchange.market_open(coin, is_buy, quantity)
                #
                # For now, use the REST API directly:
                client = await self._get_client()
                order_payload = {
                    "type": "order",
                    "orders": [{
                        "a": await self._coin_to_asset_id(coin),
                        "b": is_buy,
                        "p": str(current_price),
                        "s": str(quantity),
                        "r": False,  # reduce only
                        "t": {"limit": {"tif": "Ioc"}},  # IOC for market-like
                    }],
                    "grouping": "na",
                }

                # Sign and send (simplified — real impl needs EIP-712 signing)
                resp = await client.post(
                    HYPERLIQUID_EXCHANGE_URL,
                    json={"action": order_payload, "nonce": int(time.time() * 1000)},
                )

                if resp.status_code == 200:
                    result = resp.json()
                    return OrderResult(
                        success=True,
                        order_id=str(result.get("response", {}).get("data", {}).get("statuses", [{}])[0].get("resting", {}).get("oid", "unknown")),
                        filled_price=current_price,
                        quantity=quantity,
                        is_paper=False,
                    )
                else:
                    return OrderResult(
                        success=False,
                        error=f"HTTP {resp.status_code}: {resp.text}",
                        is_paper=False,
                    )

            return await _retry_with_backoff(_execute)

        except Exception as e:
            logger.error(f"[LIVE] Failed to open {side} on {symbol}: {e}")
            return OrderResult(success=False, error=str(e), is_paper=False)

    async def live_market_close(
        self,
        symbol: str,
        side: str,
        quantity: float,
        current_price: float,
        wallet_address: str,
        api_secret: str,
    ) -> OrderResult:
        """Close a real position on Hyperliquid."""
        try:
            async def _execute():
                coin = symbol.split("/")[0] if "/" in symbol else symbol.split("USDT")[0]
                is_buy = (side == "SHORT")  # Close SHORT = buy, close LONG = sell

                logger.info(
                    f"[LIVE] Closing {side} position: {coin} "
                    f"qty={quantity} @ market (~{current_price:.2f})"
                )

                # NOTE: For production with hyperliquid-python-sdk:
                #   exchange.market_close(coin)
                client = await self._get_client()
                order_payload = {
                    "type": "order",
                    "orders": [{
                        "a": await self._coin_to_asset_id(coin),
                        "b": is_buy,
                        "p": str(current_price),
                        "s": str(quantity),
                        "r": True,  # reduce only = close
                        "t": {"limit": {"tif": "Ioc"}},
                    }],
                    "grouping": "na",
                }

                resp = await client.post(
                    HYPERLIQUID_EXCHANGE_URL,
                    json={"action": order_payload, "nonce": int(time.time() * 1000)},
                )

                if resp.status_code == 200:
                    return OrderResult(
                        success=True,
                        filled_price=current_price,
                        quantity=quantity,
                        is_paper=False,
                    )
                else:
                    return OrderResult(
                        success=False,
                        error=f"HTTP {resp.status_code}: {resp.text}",
                        is_paper=False,
                    )

            return await _retry_with_backoff(_execute)

        except Exception as e:
            logger.error(f"[LIVE] Failed to close {side} on {symbol}: {e}")
            return OrderResult(success=False, error=str(e), is_paper=False)

    # ── Unified interface ────────────────────────────────────
    async def market_open(
        self, symbol: str, side: str, eur_amount: float,
        current_price: float, mode: str = "paper",
        wallet_address: str = "", api_secret: str = "",
    ) -> OrderResult:
        """Open a position — dispatches to paper or live."""
        usdt_amount = await self.convert_eur_to_usdt(eur_amount)

        if mode == "paper":
            return await self.paper_market_open(symbol, side, usdt_amount, current_price)
        else:
            return await self.live_market_open(
                symbol, side, usdt_amount, current_price,
                wallet_address, api_secret,
            )

    async def market_close(
        self, symbol: str, side: str, quantity: float,
        current_price: float, mode: str = "paper",
        wallet_address: str = "", api_secret: str = "",
    ) -> OrderResult:
        """Close a position — dispatches to paper or live."""
        if mode == "paper":
            return await self.paper_market_close(symbol, side, quantity, current_price)
        else:
            return await self.live_market_close(
                symbol, side, quantity, current_price,
                wallet_address, api_secret,
            )

    # ── Helpers ──────────────────────────────────────────────
    async def _fetch_asset_map(self) -> Dict[str, int]:
        """Fetch coin→asset_id mapping dynamically from Hyperliquid meta endpoint."""
        if self._asset_map_cache:
            return self._asset_map_cache
        try:
            client = await self._get_client()
            resp = await client.post(
                HYPERLIQUID_INFO_URL,
                json={"type": "meta"},
            )
            resp.raise_for_status()
            data = resp.json()
            universe = data.get("universe", [])
            self._asset_map_cache = {
                asset["name"]: idx for idx, asset in enumerate(universe)
            }
            # Also build size decimals from szDecimals
            self._size_decimals_cache = {
                asset["name"]: asset.get("szDecimals", 3) for asset in universe
            }
            logger.info(f"Fetched Hyperliquid meta: {len(universe)} assets")
            return self._asset_map_cache
        except Exception as e:
            logger.warning(f"Failed to fetch Hyperliquid meta: {e}, using fallback")
            return _FALLBACK_ASSET_MAP

    async def _coin_to_asset_id(self, coin: str) -> int:
        """Map coin name to Hyperliquid asset index (dynamic with fallback)."""
        asset_map = await self._fetch_asset_map()
        if coin not in asset_map:
            raise ValueError(
                f"Unknown coin '{coin}' — not found in Hyperliquid universe. "
                f"Known coins: {', '.join(sorted(asset_map.keys())[:20])}..."
            )
        return asset_map[coin]

    async def _get_size_decimals(self, coin: str) -> int:
        """Size decimal precision per asset on Hyperliquid (dynamic with fallback)."""
        if not self._size_decimals_cache:
            await self._fetch_asset_map()
        return self._size_decimals_cache.get(coin, _FALLBACK_SIZE_DECIMALS.get(coin, 3))


# Fallback maps used when the meta endpoint is unavailable
_FALLBACK_ASSET_MAP = {
    "BTC": 0, "ETH": 1, "SOL": 4, "BNB": 11,
    "DOGE": 5, "XRP": 8, "ADA": 9, "AVAX": 6,
    "LINK": 7, "DOT": 10, "MATIC": 3, "UNI": 12,
}
_FALLBACK_SIZE_DECIMALS = {
    "BTC": 5, "ETH": 4, "SOL": 2, "BNB": 3,
    "DOGE": 0, "XRP": 1, "ADA": 0, "AVAX": 2,
    "LINK": 2, "DOT": 1, "MATIC": 0, "UNI": 2,
}

# Backward-compatible singleton — delegates to centralized dependencies
def __getattr__(name):
    if name == "hyperliquid_client":
        from ..dependencies import get_hyperliquid_client
        return get_hyperliquid_client()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
