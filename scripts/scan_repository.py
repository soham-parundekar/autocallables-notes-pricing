"""Pre-publication scan: secrets, stray artifacts, and documentation that has drifted.

Run before any push. Three independent checks, each of which has caught
something real in this project.

Secrets. Looks for key-shaped strings and credential-shaped assignments. This
project needs no credentials at all - every source is free and unauthenticated -
so any hit is a mistake rather than a configuration choice.

Development residue. Scratch notebooks, abandoned scripts, backup copies,
compiled caches, and anything matching the shapes those take. A research
repository should contain the research, not the workspace it was built in.

Documentation drift. Every file path mentioned in the README, the report and the
source register has to exist. This is the check that fails most often, because
documentation is written while the layout is still moving.

There is also a vocabulary check with two deliberate, documented exceptions:

  "Martini, Claude"  the first name of a cited researcher, in a citation whose
                     metadata was verified against arXiv. Editing it would
                     falsify a reference, which is worse than matching a
                     keyword.

  "User-Agent"       the HTTP header. The SEC and Cboe both ask callers to
                     identify themselves, and the requests fail without it.

Both are listed explicitly rather than handled by a loose pattern, so a genuine
hit cannot hide behind them.
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

SKIP_DIRS = {".git", "__pycache__", ".pytest_cache", ".ipynb_checkpoints",
             "edgar_cache", ".venv", "venv", ".bibcache"}
# .tex and .cls joined this set when the manuscript moved into the repository.
# Leaving them out meant the scan passed over every word of the paper while
# reporting the repository clean, which is worse than not running it.
TEXT_SUFFIXES = {".py", ".md", ".yaml", ".yml", ".txt", ".bib", ".csv", ".ipynb",
                 ".toml", ".cfg", ".in", ".tex", ".cls", ".sty"}

SECRET_PATTERNS = [
    (r"AKIA[0-9A-Z]{16}", "AWS access key"),
    (r"(?i)\b(api[_-]?key|secret|passwd|password|token)\s*[:=]\s*['\"][^'\"]{8,}['\"]",
     "credential assignment"),
    (r"-----BEGIN [A-Z ]*PRIVATE KEY-----", "private key"),
    (r"(?i)\bBearer\s+[A-Za-z0-9._-]{20,}", "bearer token"),
]

RESIDUE_PATTERNS = [
    "*.bak", "*.orig", "*.tmp", "*.swp", "*~", "*copy*.py", "*_old.*",
    "*_v2.*", "*_final.*", "scratch*", "untitled*", "Untitled*", "test_scratch*",
]

ALLOWED_VOCABULARY = {
    # (file suffix or path fragment, the exact text allowed, why)
    ("references.bib", "martini, claude", "cited researcher's given name"),
    ("", "user-agent", "HTTP header required by the SEC and Cboe"),
    ("", "user_agent", "HTTP header constant"),
    # The party a term sheet appoints to fix levels and compute payoffs. It is
    # the issuer's own wording, quoted from the Morgan Stanley supplement.
    ("", "calculation agent", "the term sheet's name for the party that computes payoffs"),
    ("scan_repository.py", "", "this scanner names the terms it looks for"),
    # The manuscript carries a generative-AI-use declaration, in the same form
    # and the same position as the author's previous paper. Disclosing tool use
    # is the honest choice for an academic venue, and it is the one place in the
    # repository where this vocabulary is intended rather than accidental.
    ("declarations.tex", "declaration of generative ai use", "the manuscript's required disclosure"),
    # The disclosure has to name the tool to be a disclosure at all. The
    # exception is written as the whole clause rather than the bare product name
    # so that the word appearing anywhere else in the manuscript still fails.
    ("declarations.tex", "the author used claude", "the disclosure names the tool it discloses"),
    ("main.tex", "declaration of generative ai use", "the manuscript's required disclosure"),
    ("main.tex", "generative-ai-use declaration", "comment describing that disclosure"),
    ("declarations.tex", "generative-ai-use declaration", "comment describing that disclosure"),
}

VOCABULARY = [
    "claude", "anthropic", "chatgpt", "openai", "copilot", "llm",
    "language model", "artificial intelligence", "ai-generated", "ai-assisted",
    "ai-written", "model-generated", "prompt engineering", "system prompt",
    "agentic", "orchestration",
]
BOUNDED_VOCABULARY = ["ai", "agent", "agents", "prompt", "prompts"]


def project_files(root: Path):
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        if path.suffix.lower() in {".gz", ".png", ".pdf", ".htm", ".html"}:
            continue
        if path.suffix.lower() in TEXT_SUFFIXES or path.name in {"Makefile", ".gitignore"}:
            yield path


def read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def scan_secrets(root: Path) -> list[str]:
    findings = []
    for path in project_files(root):
        text = read(path)
        for pattern, label in SECRET_PATTERNS:
            for match in re.finditer(pattern, text):
                line = text.count("\n", 0, match.start()) + 1
                findings.append(f"{path.relative_to(root)}:{line}  {label}")
    return findings


def _ignored_patterns(root: Path) -> list[str]:
    source = root / ".gitignore"
    if not source.exists():
        return []
    return [
        line.strip().rstrip("/")
        for line in source.read_text().splitlines()
        if line.strip() and not line.startswith(("#", "!"))
    ]


def scan_residue(root: Path) -> list[str]:
    """Flag leftovers that would actually reach the commit.

    Anything .gitignore already excludes is not residue: compiled caches exist
    in any working copy and reporting them would train the reader to ignore this
    check, which is the opposite of what it is for.
    """
    ignored = _ignored_patterns(root)

    def is_ignored(path: Path) -> bool:
        relative = str(path.relative_to(root))
        for pattern in ignored:
            bare = pattern.strip("*/")
            if bare and bare in relative.split("/"):
                return True
            if path.match(pattern):
                return True
        return False

    findings = []
    for pattern in RESIDUE_PATTERNS:
        for path in root.rglob(pattern):
            if any(part in SKIP_DIRS for part in path.parts) or is_ignored(path):
                continue
            findings.append(f"{path.relative_to(root)}  matches '{pattern}'")
    return sorted(set(findings))


def scan_vocabulary(root: Path) -> tuple[list[str], list[str]]:
    flagged, allowed = [], []
    for path in project_files(root):
        text = read(path)
        lower = text.lower()
        lines = text.splitlines()
        name = path.name

        def record(term: str, position: int) -> None:
            line_no = lower.count("\n", 0, position) + 1
            content = lines[line_no - 1].strip()[:100] if line_no <= len(lines) else ""
            entry = f"{path.relative_to(root)}:{line_no}  [{term}]  {content}"
            for fragment, allowed_text, reason in ALLOWED_VOCABULARY:
                if fragment and fragment not in str(path):
                    continue
                if not allowed_text:
                    allowed.append(f"{entry}   ({reason})")
                    return
                if allowed_text in content.lower():
                    allowed.append(f"{entry}   ({reason})")
                    return
            flagged.append(entry)

        for term in VOCABULARY:
            for match in re.finditer(re.escape(term), lower):
                record(term, match.start())
        for term in BOUNDED_VOCABULARY:
            for match in re.finditer(r"\b" + re.escape(term) + r"\b", lower):
                record(term, match.start())
    return flagged, allowed


def scan_documentation(root: Path) -> list[str]:
    """Every path the documentation mentions must exist."""
    findings = []
    path_like = re.compile(r"`?((?:src|tests|scripts|config|data|references|reports)/[\w./{}-]+)`?")
    for document in ("README.md", "reports/report.md", "references/references.md",
                     "references/source_register.csv", "Makefile"):
        source = root / document
        if not source.exists():
            continue
        text = read(source)
        for match in path_like.finditer(text):
            mentioned = match.group(1).rstrip(".,)`").rstrip("/")
            if any(ch in mentioned for ch in "{}*"):
                continue
            # "data/1419828/..." inside an EDGAR archive URL is not a path in
            # this repository. Anything preceded by a URL scheme or a slash is
            # part of a link.
            prefix = text[max(0, match.start() - 60):match.start()]
            if "http://" in prefix or "https://" in prefix or prefix.endswith("/"):
                continue
            if not (root / mentioned).exists():
                findings.append(f"{document} refers to {mentioned}, which does not exist")
    return sorted(set(findings))


def main(root: Path) -> int:
    print(f"Scanning {root}\n")
    failures = 0

    for title, findings in [
        ("Secrets and credentials", scan_secrets(root)),
        ("Development residue", scan_residue(root)),
        ("Documentation references", scan_documentation(root)),
    ]:
        print(f"{title}: ", end="")
        if findings:
            failures += len(findings)
            print(f"{len(findings)} issue(s)")
            for item in findings:
                print(f"    {item}")
        else:
            print("clean")

    flagged, allowed = scan_vocabulary(root)
    print(f"Vocabulary: {len(flagged)} to review, {len(allowed)} allowed by documented exception")
    for item in flagged:
        print(f"    {item}")
    for item in allowed:
        print(f"    (ok) {item}")
    failures += len(flagged)

    print(f"\n{'PASS' if failures == 0 else f'{failures} issue(s) to resolve'}")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    raise SystemExit(main(parser.parse_args().root))
