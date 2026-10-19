"""Does the snapshot manifest actually cover the chains the results rest on?

The manifest is the only thing that makes the captured option data verifiable:
it records, per chain, the session it belongs to, the underlying close, the
contract count and a SHA-256 of the payload. A clone that reproduces the
published numbers is relying on those checksums to say the files are the ones
the results were built from.

It stopped being true without anything failing. Capturing a second batch of
symbols into a session that already had a manifest overwrote it instead of
merging, so the chains pulled in the first batch kept their files and lost
their records. The surfaces are fitted to five of those chains, and the report
claimed checksums for all of them.

Nothing about that was visible from the outside: the pipeline ran, the numbers
came out, and only counting the rows against the files showed it. Hence this.
"""

from __future__ import annotations

import csv
import gzip
import hashlib
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

CHAINS = ROOT / "data/raw/option_chains"

pytestmark = pytest.mark.skipif(
    not CHAINS.exists(), reason="no option chain snapshot in this checkout"
)


def sessions() -> list[Path]:
    return [p for p in sorted(CHAINS.glob("*/")) if any(p.glob("*.json.gz"))]


def manifest_rows(session: Path) -> list[dict]:
    path = session / "manifest.csv"
    if not path.exists():
        return []
    return list(csv.DictReader(path.read_text().splitlines()))


@pytest.mark.parametrize("session", sessions(), ids=lambda p: p.name)
def test_every_saved_chain_is_listed_in_the_manifest(session: Path):
    """A file with no manifest row is a file nothing can verify.

    The reverse is allowed and normal. The manifest records the whole capture,
    while .gitignore ships only the chains the published results are built
    from, so a clone holds eight files against a hundred-odd rows. Asserting
    the other direction would fail for everyone who clones the repository,
    which is the whole point of shipping the checksums.
    """
    rows = manifest_rows(session)
    assert rows, f"{session.name} has chains but no manifest"
    listed = {row["ticker"] for row in rows}
    saved = {path.name.removesuffix(".json.gz") for path in session.glob("*.json.gz")}

    missing = sorted(saved - listed)
    assert not missing, (
        f"{session.name}: {len(missing)} saved chain(s) are absent from the "
        f"manifest, so nothing records their checksum: {missing[:12]}"
    )


@pytest.mark.parametrize("session", sessions(), ids=lambda p: p.name)
def test_payload_checksums_match_the_manifest(session: Path):
    """The checksum is over the decompressed payload, as save_snapshot wrote it.

    Only rows whose file is present are checked, so this passes in a clone and
    in the full working copy alike, and in both it is checking every file that
    is actually there.
    """
    checked, mismatched = 0, []
    for row in manifest_rows(session):
        path = session / f"{row['ticker']}.json.gz"
        if not path.exists():
            continue
        checked += 1
        if hashlib.sha256(gzip.open(path, "rb").read()).hexdigest() != row["sha256"]:
            mismatched.append(row["ticker"])

    assert checked, f"{session.name}: no chain file matched a manifest row"
    assert not mismatched, (
        f"{session.name}: payload does not match the recorded checksum for "
        f"{mismatched}. The file has changed since it was captured."
    )


def test_every_underlying_the_project_prices_is_in_the_snapshot():
    """The chains the surfaces are fitted to, plus the ETF proxies they are
    checked against, all have to survive the .gitignore whitelist into a clone."""
    cfg = yaml.safe_load((ROOT / "config/params.yaml").read_text())
    session = CHAINS / str(cfg["snapshot"]["date"])
    if not session.exists():
        pytest.skip(f"{session.name} is not in this checkout")

    needed = {spec["chain"] for spec in cfg["underlyings"].values()}
    # src/vol/proxy_check.py compares each cash index against its ETF.
    from src.vol.proxy_check import PROXY_PAIRS

    for index_ticker, etf_ticker in PROXY_PAIRS:
        needed.update({index_ticker, etf_ticker})

    listed = {row["ticker"] for row in manifest_rows(session)}
    missing = sorted(needed - listed)
    assert not missing, (
        f"the project reads {missing} but the manifest does not list them, so a "
        f"clone cannot verify the data the published numbers came from"
    )
