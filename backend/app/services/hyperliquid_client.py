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
        """Get current EUR/USDT exchange rate. Falls back to fixed rate."""
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
                logger.info(f"EUR/USDT rate: {rate:.4f}")
                return rate
        except Exception as e:
            logger.warning(f"Failed to fetch EUR/USDT rate: {e}, using fallback")
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
                sz_decimals = self._get_size_decimals(coin)
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
                        "a": self._coin_to_asset_id(coin),
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
                        "a": self._coin_to_asset_id(coin),
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
    @staticmethod
    def _get_size_decimals(coin: str) -> int:
        """Size decimal precision per asset on Hyperliquid."""
        decimals_map = {
            "BTC": 5, "ETH": 4, "SOL": 2, "BNB": 3,
            "DOGE": 0, "XRP": 1, "ADA": 0, "AVAX": 2,
            "LINK": 2, "DOT": 1, "MATIC": 0, "UNI": 2,
        }
        return decimals_map.get(coin, 3)

    @staticmethod
    def _coin_to_asset_id(coin: str) -> int:
        """Map coin name to Hyperliquid asset index."""
        # This mapping should be fetched dynamically from Hyperliquid meta endpoint
        asset_map = {
            "BTC": 0, "ETH": 1, "SOL": 4, "BNB": 11,
            "DOGE": 5, "XRP": 8, "ADA": 9, "AVAX": 6,
            "LINK": 7, "DOT": 10, "MATIC": 3, "UNI": 12,
        }
        return asset_map.get(coin, 0)


# Singleton
hyperliquid_client = HyperliquidClient()
