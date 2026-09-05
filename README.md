# kalshi-post-only-bot

A small, honest starting point for a config-driven Kalshi bot that only ever enters with post-only limit bids, never holds a second ticket in the same market, and exits with a bot-sent sell when last price trades through a stop, because Kalshi has no native stop order.

This repository is milestone 1 in code form: authentication against Kalshi's official Trade API v2 (RSA-PSS signed requests), reading markets on an allow-list, placing one buy limit, post-only, GTC order on the demo exchange, confirming whether it is working or filled, and cancelling it. Nothing here scrapes the website or uses unofficial endpoints.

## What is in here

| file | purpose |
|---|---|
| `kalshi_client.py` | The client: signing, markets, orderbook, balance, positions, fills, orders, `place_post_only_bid`, `sell_at_or_below`, `cancel`, and `working_vs_filled` (fill detection by reconciliation). Run it directly for the milestone 1 demo. |
| `config.example.yaml` | Every number the strategy needs lives here, not in code: allowed series and markets, price pair, order size, max open tickets (1 to 4), stop level, on and off hours, alert targets, kill file path. |
| `test_signing.py` | Proves the RSA-PSS recipe against a freshly generated key: signature verifies, wrong path or method does not. Runs offline. |
| `requirements.txt` | `cryptography`, `requests`, `PyYAML`, `websockets`. |

## Keys stay off the developer's machine

- The client generates the API key pair in their Kalshi account and puts the private key file on the VPS with mode `600`. The bot reads `KALSHI_KEY_ID` and `KALSHI_PRIVATE_KEY_PATH` from the environment (`.env` on the server, owned by the client, ignored by git).
- Development happens against the demo exchange with a demo key that the client can revoke the day of handoff. Production keys are never sent over chat or email, never stored in the repository, and never copied to the developer's machine; the developer's VPS login is removed at handoff and the client rotates the key.
- `.gitignore` excludes `.env`, `*.pem`, `*.key`, logs and snapshots.

## Fill versus working bid

Two independent sources have to agree before the bot changes state:

1. `GET /portfolio/orders/{order_id}` gives `fill_count` and `remaining_count`; a bid is working while it is `resting` with `remaining_count` above zero.
2. `GET /portfolio/fills?order_id=...` lists the actual fills for that order; the bot sums `count` and treats any fill, partial or full, as filled size.

The websocket `fill` channel delivers the same event in real time; the REST reconciliation runs on every loop tick so a dropped websocket cannot leave the bot believing it has a working bid that actually filled.

## Stop exit when last trades through the level

The bot subscribes to the market's `ticker` channel and watches `last_price`. When last trades at or through the configured stop, it sends an ask for the filled size at the stop price (or the current bid, whichever is lower), immediate-or-cancel, `reduce_only`, not post-only, so it crosses the book and exits rather than resting. If the IOC does not fill completely, it re-sends against the fresh bid until flat or until the kill switch is hit; every attempt is logged.

## One ticket per market, max tickets configurable

State is keyed by market ticker. A market with any working bid or any open position is closed to new entries. `max_open_tickets` (default 1, up to 4) counts distinct markets with a working bid or a fill.

## Running the milestone 1 demo

```
pip install -r requirements.txt
export KALSHI_KEY_ID=...            # key id only
export KALSHI_PRIVATE_KEY_PATH=/etc/kalshi/demo.pem
python kalshi_client.py --series KXMLBGAME --price 0.05 --count 1
```

It prints the balance, the market's bid, ask and last, the placed order, the working-or-filled state, and the cancel result.

## What this is not

Not a strategy, not a signal service, not an "AI trader". It is plumbing that reads a config and obeys it.
