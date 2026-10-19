"""Download the rates, index and spread series the pricing depends on.

Separate from the option-chain snapshot because these are all genuine history:
rerunning this tomorrow returns the same values for 2026-10-07, whereas the
option chains are a snapshot that cannot be recovered once the session passes.

Every source here is free and, apart from the SMH series, official. The SEC and
Cboe both ask callers to identify themselves, so a contact string is sent with
each request.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import requests

USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0 Safari/537.36"
)

TREASURY_URL = (
    "https://home.treasury.gov/resource-center/data-chart-center/interest-rates/"
    "daily-treasury-rates.csv/{year}/all?type=daily_treasury_yield_curve"
    "&field_tdr_date_value={year}&page&_format=csv"
)
CBOE_INDEX_URL = "https://cdn.cboe.com/api/global/us_indices/daily_prices/{series}_History.csv"
FRED_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv?id={series}"

# Cboe volatility indices, used to measure the surface move over the gap between
# each note's pricing date and the option snapshot.
CBOE_VOL_INDICES = {"VIX": "cboe_vix_history.csv", "VXN": "cboe_vxn_history.csv",
                    "RVX": "cboe_rvx_history.csv"}

# Russell 2000 levels come from Cboe because FRED carries no equivalent series.
CBOE_LEVELS = {"RUT": "cboe_rut_history.csv"}

FRED_SERIES = {
    "SP500": "fred_SP500.csv",
    "NASDAQ100": "fred_NASDAQ100.csv",
    "DJIA": "fred_DJIA.csv",
    # ICE BofA A-rated corporate index option-adjusted spread: the benchmark the
    # implied funding spreads are compared against. These issuers' senior
    # unsecured ratings sit in the A band.
    "BAMLC0A3CA": "fred_BAMLC0A3CA.csv",
}


def download(url: str, target: Path, *, contact: str, timeout: int = 90) -> int:
    headers = {"User-Agent": contact or USER_AGENT}
    response = requests.get(url, headers=headers, timeout=timeout)
    response.raise_for_status()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(response.content)
    return len(response.content)


def main(raw_dir: Path, *, year: int, contact: str) -> None:
    print(f"Treasury par yield curve {year}")
    size = download(TREASURY_URL.format(year=year), raw_dir / f"treasury_par_yields_{year}.csv",
                    contact=contact)
    print(f"  treasury_par_yields_{year}.csv  {size/1e3:.1f} kB")

    for series, filename in {**CBOE_VOL_INDICES, **CBOE_LEVELS}.items():
        size = download(CBOE_INDEX_URL.format(series=series), raw_dir / filename, contact=contact)
        print(f"  {filename:<28} {size/1e3:>7.1f} kB")

    for series, filename in FRED_SERIES.items():
        size = download(FRED_URL.format(series=series), raw_dir / filename, contact=contact)
        print(f"  {filename:<28} {size/1e3:>7.1f} kB")

    # SMH daily closes. No free official series exists for a single ETF, and
    # Yahoo's endpoint needs the cookie and crumb handshake that the yfinance
    # package implements, so a plain request is not enough here.
    try:
        import warnings

        import yfinance

        warnings.filterwarnings("ignore")
        history = yfinance.Ticker("SMH").history(period="2y", auto_adjust=False)[["Close"]]
        history.index = history.index.tz_localize(None)
        history.to_csv(raw_dir / "yahoo_SMH_daily.csv")
        print(f"  yahoo_SMH_daily.csv          {len(history):>7} rows, "
              f"last {history.index[-1].date()}")
    except Exception as error:  # noqa: BLE001
        print(f"  SMH daily closes unavailable: {error}")
        print("  Needed only for the one note with an ETF underlying (N6).")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--raw", type=Path, default=Path("data/raw"))
    parser.add_argument("--year", type=int, default=2026)
    parser.add_argument(
        "--contact", default="",
        help="name and email; the SEC and Cboe ask callers to identify themselves",
    )
    args = parser.parse_args()
    main(args.raw, year=args.year, contact=args.contact)
