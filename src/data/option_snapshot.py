"""Close-of-day option chain snapshots from Cboe's delayed-quote feed.

The whole project rests on this file running on the right day. Free option data
is a snapshot of now; nobody gives away last month's chains. So a note can only
be priced honestly if we were already holding a chain for its pricing date, and
that makes the snapshot archive the one artifact we cannot rebuild later.

Cboe is used rather than a retail aggregator for two reasons. It is the listing
venue for SPX, NDX and RUT (those are Cboe-exclusive index options, so this is
the primary source rather than a redistribution of it), and the payload carries
bid, ask, both quote sizes and open interest per contract. Quote sizes matter
because the cleaning step in src/vol/forwards.py leans on them to throw out
strikes that are quoted but not really tradable.

Raw payloads are written to disk byte-for-byte and never edited in place. Every
transformation happens downstream of data/raw/, which is what lets the manifest
checksums mean something.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import re
import time
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

import pandas as pd
import requests

# cdn.cboe.com answers every request with a 307 to this host. Requesting the
# final host directly skips a redirect hop per symbol.
CBOE_QUOTE_URL = "https://cdn-api.cboe.com/api/global/delayed_quotes/options/{cboe_symbol}.json"

# Cboe namespaces cash indices with a leading underscore and quotes everything
# else bare. Getting this wrong returns a 404 rather than the wrong product,
# which at least fails loudly.
CASH_INDEX_ROOTS = frozenset({"SPX", "NDX", "RUT", "DJX", "VIX", "MID", "OEX", "XSP", "RUI"})

# OCC-style contract names, e.g. SPY261218C00700000 -> SPY, 2026-12-18, call, 700.0
_CONTRACT_NAME = re.compile(r"^(?P<root>[A-Z]+)(?P<yy>\d{2})(?P<mm>\d{2})(?P<dd>\d{2})(?P<kind>[CP])(?P<strike>\d{8})$")

# Strike is encoded in thousandths of a dollar.
_STRIKE_SCALE = 1000.0

_REQUEST_HEADERS = {
    # Cboe's CDN sits behind Cloudflare and drops requests with no User-Agent.
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0 Safari/537.36",
    "Accept": "application/json",
}


def to_cboe_symbol(ticker: str) -> str:
    """Map a plain ticker onto Cboe's quote symbol."""
    root = ticker.lstrip("^_").upper()
    return f"_{root}" if root in CASH_INDEX_ROOTS else root


@dataclass(frozen=True)
class ChainSnapshot:
    """One symbol's chain as pulled, with enough context to audit it later."""

    ticker: str
    cboe_symbol: str
    feed_timestamp: str      # Cboe's own publish time for the payload
    last_trade_time: str     # last print on the underlying; the real date anchor
    underlying_close: float
    security_type: str
    raw_path: Path
    sha256: str
    n_contracts: int

    @property
    def quote_date(self) -> date:
        """Session the quotes belong to, taken from the underlying's last print.

        The feed timestamp is when Cboe published the file, which on a weekend
        pull is a day or two after the session being quoted. The underlying's
        last trade is the honest anchor, so that is what dates the snapshot.
        """
        return datetime.fromisoformat(self.last_trade_time).date()


def fetch_chain(
    ticker: str,
    *,
    session: requests.Session | None = None,
    attempts: int = 4,
    timeout: int = 120,
) -> bytes:
    """Pull one symbol's full chain, retrying on throttling and timeouts.

    Cboe does not publish a rate limit, but pulling sixty symbols back to back
    draws occasional 403s and 429s from Cloudflare. Backoff is deliberately
    patient: losing a symbol costs a whole underlying for the week, while
    waiting a few extra seconds costs nothing.
    """
    http = session or requests.Session()
    url = CBOE_QUOTE_URL.format(cboe_symbol=to_cboe_symbol(ticker))
    last_error: Exception | None = None

    for attempt in range(attempts):
        try:
            response = http.get(url, headers=_REQUEST_HEADERS, timeout=timeout)
            if response.status_code == 404:
                raise LookupError(f"Cboe has no chain for {ticker} (tried {url})")
            if response.status_code in (403, 429, 500, 502, 503, 504):
                raise requests.HTTPError(f"HTTP {response.status_code} for {ticker}")
            response.raise_for_status()
            # Guard against a Cloudflare challenge page arriving with a 200.
            if not response.content.lstrip().startswith(b"{"):
                raise requests.HTTPError(f"{ticker}: expected JSON, got {response.content[:80]!r}")
            return response.content
        except LookupError:
            raise
        except Exception as exc:  # noqa: BLE001 - retry on anything transient
            last_error = exc
            if attempt < attempts - 1:
                time.sleep(3 * (attempt + 1))

    raise RuntimeError(f"Could not fetch {ticker} after {attempts} attempts: {last_error}")


def save_snapshot(ticker: str, payload: bytes, snapshot_dir: Path) -> ChainSnapshot:
    """Write the raw payload under snapshot_dir and pull out its header fields."""
    parsed = json.loads(payload)
    header = parsed["data"]

    snapshot_dir.mkdir(parents=True, exist_ok=True)
    raw_path = snapshot_dir / f"{ticker.upper()}.json.gz"
    # mtime=0 so re-running the pull on an unchanged payload gives an identical
    # file, which keeps the checksums in the manifest stable.
    with gzip.GzipFile(raw_path, "wb", mtime=0) as handle:
        handle.write(payload)

    return ChainSnapshot(
        ticker=ticker.upper(),
        cboe_symbol=to_cboe_symbol(ticker),
        feed_timestamp=parsed["timestamp"],
        last_trade_time=header["last_trade_time"],
        underlying_close=float(header["close"]),
        security_type=header["security_type"],
        raw_path=raw_path,
        sha256=hashlib.sha256(payload).hexdigest(),
        n_contracts=len(header["options"]),
    )


def parse_chain(payload: bytes, ticker: str) -> pd.DataFrame:
    """Flatten a raw payload into one row per contract.

    Cboe ships its own iv/delta/gamma/theta columns. They are carried through
    for cross-checking only. Every implied vol this project calibrates on is
    solved in src/vol/forwards.py from the mid price, because a vendor's vol
    silently embeds that vendor's forward and dividend assumptions, and the
    forward is precisely what Module B is trying to measure.
    """
    parsed = json.loads(payload)
    header = parsed["data"]
    rows = []

    for contract in header["options"]:
        name = contract["option"]
        match = _CONTRACT_NAME.match(name)
        if match is None:
            # Non-standard roots turn up after corporate actions. Skip them
            # loudly in the row count rather than guessing at the strike.
            continue
        rows.append(
            {
                "ticker": ticker.upper(),
                "contract": name,
                "expiry": date(
                    2000 + int(match["yy"]), int(match["mm"]), int(match["dd"])
                ),
                "right": match["kind"],
                "strike": int(match["strike"]) / _STRIKE_SCALE,
                "bid": contract["bid"],
                "ask": contract["ask"],
                "bid_size": contract["bid_size"],
                "ask_size": contract["ask_size"],
                "volume": contract["volume"],
                "open_interest": contract["open_interest"],
                "last_trade_price": contract["last_trade_price"],
                "cboe_iv": contract["iv"],
                "cboe_delta": contract["delta"],
            }
        )

    chain = pd.DataFrame(rows)
    chain["underlying_close"] = float(header["close"])
    chain["quote_date"] = datetime.fromisoformat(header["last_trade_time"]).date()
    return chain


def pull_universe(
    tickers: list[str],
    snapshot_root: Path,
    *,
    pause: float = 1.0,
) -> tuple[pd.DataFrame, list[str]]:
    """Snapshot every ticker into a dated folder and write a manifest.

    Returns the manifest and the tickers that failed. One bad symbol does not
    abort the run: on a pricing day the other forty are still worth keeping.
    """
    session = requests.Session()
    records, failures = [], []

    for ticker in tickers:
        try:
            payload = fetch_chain(ticker, session=session)
        except Exception as exc:  # noqa: BLE001
            print(f"  {ticker:<6} failed: {exc}")
            failures.append(ticker)
            continue

        # The folder is named for the session the quotes belong to, not for the
        # day the script ran, so a weekend pull lands in the Friday folder.
        probe = json.loads(payload)["data"]
        quote_day = datetime.fromisoformat(probe["last_trade_time"]).date()
        snapshot = save_snapshot(ticker, payload, snapshot_root / quote_day.isoformat())

        records.append(
            {
                "ticker": snapshot.ticker,
                "cboe_symbol": snapshot.cboe_symbol,
                "quote_date": snapshot.quote_date.isoformat(),
                "feed_timestamp": snapshot.feed_timestamp,
                "last_trade_time": snapshot.last_trade_time,
                "underlying_close": snapshot.underlying_close,
                "security_type": snapshot.security_type,
                "n_contracts": snapshot.n_contracts,
                "sha256": snapshot.sha256,
                "raw_path": str(snapshot.raw_path.relative_to(snapshot_root.parent.parent)),
            }
        )
        print(
            f"  {snapshot.ticker:<6} {snapshot.n_contracts:>6} contracts  "
            f"close={snapshot.underlying_close:>10.2f}  session={snapshot.quote_date}"
        )
        time.sleep(pause)

    manifest = pd.DataFrame(records)
    if not manifest.empty:
        for quote_day, group in manifest.groupby("quote_date"):
            target = snapshot_root / str(quote_day) / "manifest.csv"
            # Merge rather than overwrite. Capturing a second batch of symbols
            # into a session that already has a manifest used to replace it,
            # which silently dropped the checksums for everything pulled in the
            # first batch: the snapshot kept the files and lost their record.
            merged = group
            if target.exists():
                existing = pd.read_csv(target)
                merged = pd.concat([existing, group], ignore_index=True)
                merged = merged.drop_duplicates(subset="ticker", keep="last")
            merged.sort_values("ticker").to_csv(target, index=False)

    return manifest, failures


def rebuild_manifest(snapshot_dir: Path) -> pd.DataFrame:
    """Reconstruct a session's manifest from the saved payloads.

    Every field in the manifest comes out of the payload itself, so a manifest
    that has gone missing or incomplete can be rebuilt from the files without
    going back to the feed. The checksums are taken over the decompressed
    payload, the same bytes `save_snapshot` hashed, so a rebuilt row matches
    what the original pull recorded.
    """
    records = []
    for path in sorted(snapshot_dir.glob("*.json.gz")):
        payload = gzip.open(path, "rb").read()
        parsed = json.loads(payload)
        header = parsed["data"]
        ticker = path.name.removesuffix(".json.gz")
        last_trade = header["last_trade_time"]
        records.append({
            "ticker": ticker,
            "cboe_symbol": to_cboe_symbol(ticker),
            "quote_date": str(last_trade)[:10],
            "feed_timestamp": parsed["timestamp"],
            "last_trade_time": last_trade,
            "underlying_close": float(header["close"]),
            "security_type": header["security_type"],
            "n_contracts": len(header["options"]),
            "sha256": hashlib.sha256(payload).hexdigest(),
            "raw_path": f"raw/option_chains/{snapshot_dir.name}/{path.name}",
        })

    manifest = pd.DataFrame(records).sort_values("ticker")
    manifest.to_csv(snapshot_dir / "manifest.csv", index=False)
    return manifest


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("tickers", nargs="*", help="plain tickers, e.g. SPX NDX RUT XLE")
    parser.add_argument(
        "--out",
        type=Path,
        default=Path("data/raw/option_chains"),
        help="root for dated snapshot folders",
    )
    parser.add_argument(
        "--rebuild-manifest", action="store_true",
        help="rewrite each session's manifest from the payloads already saved, "
             "without calling the feed",
    )
    args = parser.parse_args()

    if args.rebuild_manifest:
        for session in sorted(args.out.glob("*/")):
            if not any(session.glob("*.json.gz")):
                continue
            rebuilt = rebuild_manifest(session)
            print(f"  {session.name}: {len(rebuilt)} chains")
        raise SystemExit(0)

    manifest, failures = pull_universe(args.tickers, args.out)
    print(f"\nsaved {len(manifest)} chains, {len(failures)} failed")
    if failures:
        print("failed:", " ".join(failures))
