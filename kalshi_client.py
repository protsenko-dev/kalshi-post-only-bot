"""Minimal Kalshi Trade API v2 client: RSA-PSS signed requests, markets, post-only orders, cancels, fills.

Built as the demo for milestone 1 of a config-driven Kalshi bot:
  - authenticate with an API key id + private key file (never embedded in code or git)
  - read markets on an allow-list
  - place ONE buy limit, post-only, GTC bid at a config price on the demo exchange
  - confirm it is resting (working) or filled, then cancel it

Python 3.11+. Dependencies: cryptography, requests (see requirements.txt).
"""
from __future__ import annotations

import base64
import json
import os
import time
from dataclasses import dataclass
from typing import Any, Optional

import requests
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

PROD_BASE = "https://external-api.kalshi.com/trade-api/v2"
DEMO_BASE = "https://external-api.demo.kalshi.co/trade-api/v2"
PROD_WS = "wss://external-api-ws.kalshi.com/trade-api/ws/v2"
DEMO_WS = "wss://external-api-ws.demo.kalshi.co/trade-api/ws/v2"


@dataclass
class KalshiCredentials:
    key_id: str
    private_key_path: str

    @classmethod
    def from_env(cls) -> "KalshiCredentials":
        """Key id and key PATH come from the environment; the key file lives on the VPS with mode 600."""
        key_id = os.environ.get("KALSHI_KEY_ID")
        path = os.environ.get("KALSHI_PRIVATE_KEY_PATH")
        if not key_id or not path:
            raise RuntimeError("KALSHI_KEY_ID and KALSHI_PRIVATE_KEY_PATH must be set")
        return cls(key_id=key_id, private_key_path=path)


class KalshiClient:
    def __init__(self, creds: KalshiCredentials, demo: bool = True, timeout: float = 10.0):
        self.base = DEMO_BASE if demo else PROD_BASE
        self.ws_url = DEMO_WS if demo else PROD_WS
        self.key_id = creds.key_id
        with open(creds.private_key_path, "rb") as f:
            self._key = serialization.load_pem_private_key(f.read(), password=None)
        self.timeout = timeout
        self.session = requests.Session()

    # ---- signing -------------------------------------------------------------------------------
    def _sign(self, timestamp_ms: str, method: str, path: str) -> str:
        """RSA-PSS over timestamp + METHOD + path (path includes /trade-api/v2, excludes the query string)."""
        message = f"{timestamp_ms}{method.upper()}{path}".encode("utf-8")
        signature = self._key.sign(
            message,
            padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=hashes.SHA256().digest_size),
            hashes.SHA256(),
        )
        return base64.b64encode(signature).decode("ascii")

    def _headers(self, method: str, path: str) -> dict[str, str]:
        ts = str(int(time.time() * 1000))
        return {
            "KALSHI-ACCESS-KEY": self.key_id,
            "KALSHI-ACCESS-TIMESTAMP": ts,
            "KALSHI-ACCESS-SIGNATURE": self._sign(ts, method, path),
            "Content-Type": "application/json",
        }

    def _request(self, method: str, path: str, params: Optional[dict] = None, body: Optional[dict] = None) -> Any:
        """path is relative to /trade-api/v2, e.g. '/markets'. Retries once on 429 after the server's hint."""
        full_path = "/trade-api/v2" + path
        url = self.base + path
        for attempt in (1, 2):
            r = self.session.request(method, url, params=params, json=body, headers=self._headers(method, full_path), timeout=self.timeout)
            if r.status_code == 429 and attempt == 1:
                time.sleep(float(r.headers.get("Retry-After", "1")))
                continue
            if r.status_code >= 400:
                raise KalshiError(r.status_code, r.text[:500])
            return r.json() if r.text else {}
        raise KalshiError(429, "rate limited twice")

    # ---- reads ----------------------------------------------------------------------------------
    def markets(self, series_ticker: Optional[str] = None, status: str = "open", limit: int = 200) -> list[dict]:
        params = {"limit": limit, "status": status}
        if series_ticker:
            params["series_ticker"] = series_ticker
        return self._request("GET", "/markets", params=params).get("markets", [])

    def market(self, ticker: str) -> dict:
        return self._request("GET", f"/markets/{ticker}").get("market", {})

    def orderbook(self, ticker: str, depth: int = 10) -> dict:
        return self._request("GET", f"/markets/{ticker}/orderbook", params={"depth": depth})

    def balance(self) -> dict:
        return self._request("GET", "/portfolio/balance")

    def positions(self, ticker: Optional[str] = None) -> dict:
        params = {"ticker": ticker} if ticker else None
        return self._request("GET", "/portfolio/positions", params=params)

    def fills(self, ticker: Optional[str] = None, order_id: Optional[str] = None, limit: int = 100) -> list[dict]:
        params: dict[str, Any] = {"limit": limit}
        if ticker:
            params["ticker"] = ticker
        if order_id:
            params["order_id"] = order_id
        return self._request("GET", "/portfolio/fills", params=params).get("fills", [])

    def order(self, order_id: str) -> dict:
        return self._request("GET", f"/portfolio/orders/{order_id}").get("order", {})

    def open_orders(self, ticker: Optional[str] = None) -> list[dict]:
        params: dict[str, Any] = {"status": "resting"}
        if ticker:
            params["ticker"] = ticker
        return self._request("GET", "/portfolio/orders", params=params).get("orders", [])

    # ---- writes ---------------------------------------------------------------------------------
    def place_post_only_bid(self, ticker: str, price_dollars: str, count: int, client_order_id: str) -> dict:
        """Buy limit, post-only, GTC. Rejected by the exchange instead of filling as a taker (that is the point)."""
        body = {
            "ticker": ticker,
            "side": "bid",
            "count": str(count),
            "price": price_dollars,
            "time_in_force": "good_till_canceled",
            "post_only": True,
            "self_trade_prevention_type": "maker",
            "client_order_id": client_order_id,
        }
        return self._request("POST", "/portfolio/events/orders", body=body)

    def sell_at_or_below(self, ticker: str, price_dollars: str, count: int, client_order_id: str) -> dict:
        """The stop exit. Kalshi has no native stop: when last trades at or through the stop, this sends an ask
        that crosses the book (immediate-or-cancel, not post-only) for the filled size."""
        body = {
            "ticker": ticker,
            "side": "ask",
            "count": str(count),
            "price": price_dollars,
            "time_in_force": "immediate_or_cancel",
            "post_only": False,
            "self_trade_prevention_type": "taker_at_cross",
            "reduce_only": True,
            "client_order_id": client_order_id,
        }
        return self._request("POST", "/portfolio/events/orders", body=body)

    def cancel(self, order_id: str) -> dict:
        return self._request("DELETE", f"/portfolio/events/orders/{order_id}")


class KalshiError(Exception):
    def __init__(self, status: int, text: str):
        super().__init__(f"HTTP {status}: {text}")
        self.status = status


def working_vs_filled(client: KalshiClient, order_id: str) -> dict:
    """Fill detection without guessing: the order record's fill_count vs remaining_count, cross-checked
    against /portfolio/fills for the same order_id. A bid is 'working' while remaining_count > 0 and the
    status is resting; it is 'filled' when fill_count > 0 (partial or full). The websocket 'fill' channel
    gives the same fact in real time; this REST check is the reconciliation on every loop tick."""
    o = client.order(order_id)
    fills = client.fills(order_id=order_id)
    filled = sum(int(float(f.get("count", 0))) for f in fills)
    return {
        "order_id": order_id,
        "status": o.get("status"),
        "fill_count": o.get("fill_count"),
        "remaining_count": o.get("remaining_count"),
        "fills_seen": filled,
        "state": "filled" if filled > 0 else ("working" if o.get("status") == "resting" else o.get("status")),
    }


if __name__ == "__main__":
    import argparse
    import uuid

    ap = argparse.ArgumentParser(description="Milestone 1 demo: auth, read markets, place and cancel one post-only bid.")
    ap.add_argument("--series", default="KXMLBGAME", help="series ticker on the allow-list")
    ap.add_argument("--ticker", help="exact market ticker; default = first open market in the series")
    ap.add_argument("--price", default="0.05", help="bid price in dollars, fixed point")
    ap.add_argument("--count", type=int, default=1)
    ap.add_argument("--prod", action="store_true", help="use production (default demo)")
    args = ap.parse_args()

    c = KalshiClient(KalshiCredentials.from_env(), demo=not args.prod)
    print("balance:", json.dumps(c.balance()))
    ticker = args.ticker
    if not ticker:
        ms = c.markets(series_ticker=args.series, limit=5)
        if not ms:
            raise SystemExit("no open markets in series " + args.series)
        ticker = ms[0]["ticker"]
    print("market:", ticker, json.dumps({k: c.market(ticker).get(k) for k in ("yes_bid_dollars", "yes_ask_dollars", "last_price_dollars", "status")}))
    coid = "demo-" + uuid.uuid4().hex[:12]
    placed = c.place_post_only_bid(ticker, args.price, args.count, coid)
    print("placed:", json.dumps(placed))
    oid = placed.get("order_id")
    time.sleep(1.0)
    print("state:", json.dumps(working_vs_filled(c, oid)))
    print("cancel:", json.dumps(c.cancel(oid)))
