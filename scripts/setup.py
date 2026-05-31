#!/usr/bin/env python3
"""Cross-platform setup for mtg-proxies.

Replaces the shell-script Makefile recipes with a Python entry point that
works on Windows, macOS, and Linux from a single codebase. The Makefile is
now a thin wrapper around this — you can also call this script directly:

    python scripts/setup.py                # full install
    python scripts/setup.py --node-only    # just `npm install` in the harness dir
    python scripts/setup.py --cardconjurer-only   # just lazy-clone the CC source
    python scripts/setup.py --check        # check deps + report; do nothing else

Note: MTGPics is NOT installed by this script — its URL pattern is reimplemented
directly in mtg_proxies/cardconjourer/mtgpics.py, so there's nothing to fetch
or install. The hi-res art fetcher Just Works once the rest of the install
finishes (the only runtime dep it has is ``requests``, which ``uv sync`` already
provides).
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
NODE_DIR = REPO_ROOT / "mtg_proxies" / "cardconjourer" / "node"
CC_LOCK = REPO_ROOT / "cardconjurer.lock"
CC_CACHE = Path.home() / ".cache" / "mtg-proxies" / "cardconjurer"
CC_REPO = "https://github.com/joshbirnholz/cardconjurer.git"
# Sparse-checkout pattern: only the engine code + fonts + the 8th-frame /
# manaSymbols / setSymbols assets we actually load. Keeps the cache to ~50 MB
# instead of the full ~4 GB upstream history.
CC_SPARSE = [
    "/js/*",
    "/fonts/*",
    "/img/frames/*",
    "/img/manaSymbols/*",
    "/img/setSymbols/*",
    "/img/*.png",
    "/data/*",
]

# (binary, install_hint) — install_hint is shown only when the binary is missing.
DEPENDENCIES = [
    ("git",  "https://git-scm.com/downloads"),
    ("node", "https://nodejs.org or `nvm install --lts`"),
    ("npm",  "ships with Node.js (above)"),
    ("uv",   "https://docs.astral.sh/uv/getting-started/installation/"),
]


def _color(text: str, code: str) -> str:
    """ANSI-colorize ``text``; no-op when stdout isn't a TTY (logfiles stay clean)."""
    if not sys.stdout.isatty():
        return text
    return f"\033[{code}m{text}\033[0m"


def _ok(text: str) -> str:  return _color(text, "32")  # green
def _warn(text: str) -> str: return _color(text, "33")  # yellow
def _err(text: str) -> str:  return _color(text, "31")  # red


def check_dependencies(*, fail_on_missing: bool = True) -> list[str]:
    """Return a list of missing dependency names; print status as it goes."""
    missing: list[str] = []
    print("[setup] checking dependencies…")
    for binary, hint in DEPENDENCIES:
        path = shutil.which(binary)
        if path is None:
            print(f"  {_err('MISSING')}  {binary:8} — install: {hint}")
            missing.append(binary)
        else:
            print(f"  {_ok('OK')}       {binary:8} — {path}")
    if missing and fail_on_missing:
        print()
        print(_err(f"[setup] {len(missing)} dependency(ies) missing. "
                   "Install them and re-run."))
        sys.exit(1)
    return missing


def setup_node() -> None:
    """Run ``npm install`` in the harness's node dir."""
    print(f"[node] npm install in {NODE_DIR}")
    if not NODE_DIR.is_dir():
        print(_err(f"[node] {NODE_DIR} not found — bad checkout?"))
        sys.exit(1)
    subprocess.run(["npm", "install", "--silent"], cwd=str(NODE_DIR), check=True)
    print(f"[node] {_ok('done')}")


def setup_cardconjurer() -> None:
    """Lazy-clone Card Conjurer at the pinned SHA into the user cache dir.

    Idempotent: re-running when the cache is already at the pinned SHA does
    nothing. The partial-clone + sparse-checkout combo keeps the on-disk
    footprint to ~50 MB instead of pulling the full ~4 GB history.
    """
    if not CC_LOCK.is_file():
        print(_err(f"[cardconjurer] {CC_LOCK} missing — bad checkout?"))
        sys.exit(1)
    sha = CC_LOCK.read_text().strip()

    if (CC_CACHE / ".git").is_dir():
        # Already cloned. Verify it's at the pinned SHA; if so, skip.
        result = subprocess.run(
            ["git", "-C", str(CC_CACHE), "rev-parse", "HEAD"],
            capture_output=True, text=True, check=False,
        )
        if result.returncode == 0 and result.stdout.strip() == sha:
            print(f"[cardconjurer] cache at {CC_CACHE} already at {sha[:10]} — skipping")
            return
        print(f"[cardconjurer] cache exists but at wrong SHA — re-fetching {sha[:10]}")

    print(f"[cardconjurer] fetching {CC_REPO} @ {sha[:10]} -> {CC_CACHE} (partial + sparse)")
    CC_CACHE.mkdir(parents=True, exist_ok=True)

    if not (CC_CACHE / ".git").is_dir():
        # First-time clone. --filter=blob:none + --no-checkout to defer blob fetch
        # until after we narrow with sparse-checkout.
        subprocess.run(
            ["git", "clone", "--filter=blob:none", "--no-checkout", CC_REPO, str(CC_CACHE)],
            check=True,
        )

    subprocess.run(
        ["git", "-C", str(CC_CACHE), "sparse-checkout", "init", "--no-cone"],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(CC_CACHE), "sparse-checkout", "set", *CC_SPARSE],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(CC_CACHE), "fetch", "--depth=1", "origin", sha],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(CC_CACHE), "checkout", sha],
        check=True,
    )
    print(f"[cardconjurer] {_ok('ready')} at {CC_CACHE}")


def setup_python() -> None:
    """Run ``uv sync`` to install the Python venv + deps."""
    print("[uv] uv sync")
    subprocess.run(["uv", "sync"], cwd=str(REPO_ROOT), check=True)
    print(f"[uv] {_ok('done')}")


def clean_cardconjurer() -> None:
    """Delete the cached Card Conjurer source. Next ``setup`` re-clones."""
    if CC_CACHE.exists():
        print(f"[clean-cc] removing {CC_CACHE}")
        shutil.rmtree(CC_CACHE)
        print(f"[clean-cc] {_ok('done')}")
    else:
        print(f"[clean-cc] {CC_CACHE} doesn't exist — nothing to do")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    g = p.add_mutually_exclusive_group()
    g.add_argument("--check",              action="store_true",
                   help="check deps and exit; install nothing")
    g.add_argument("--cardconjurer-only",  action="store_true",
                   help="only lazy-clone the Card Conjurer source")
    g.add_argument("--node-only",          action="store_true",
                   help="only run `npm install` in the harness dir")
    g.add_argument("--skip-cardconjurer",  action="store_true",
                   help="full install minus the Card Conjurer clone")
    g.add_argument("--clean-cardconjurer", action="store_true",
                   help="delete the cached Card Conjurer clone")
    args = p.parse_args()

    if args.clean_cardconjurer:
        clean_cardconjurer()
        return

    # ``--check`` and ``--cardconjurer-only`` need only a subset of the deps;
    # everyone else needs the full set.
    if args.check:
        check_dependencies(fail_on_missing=False)
        return
    if args.cardconjurer_only:
        # Just git is enough.
        if shutil.which("git") is None:
            print(_err("[setup] missing: git — install from https://git-scm.com/downloads"))
            sys.exit(1)
        setup_cardconjurer()
        return
    if args.node_only:
        if shutil.which("npm") is None:
            print(_err("[setup] missing: npm — install Node from https://nodejs.org"))
            sys.exit(1)
        setup_node()
        return

    # Full install: check everything first, then run all three phases.
    check_dependencies(fail_on_missing=True)
    setup_node()
    if not args.skip_cardconjurer:
        setup_cardconjurer()
    setup_python()
    print()
    print(_ok("[setup] all done."))


if __name__ == "__main__":
    main()
