# mtg-proxies dev tasks.
#
# All real install logic lives in scripts/setup.py — this Makefile just
# dispatches. The script is pure stdlib Python and works cross-platform
# (macOS, Linux, Windows). Windows users without GNU make can call the
# script directly: `python scripts/setup.py`.
#
# Dependency note: MTGPics (the hi-res art source for `cardconjourer`) is
# NOT installed here. Its URL pattern is reimplemented directly in
# mtg_proxies/cardconjourer/mtgpics.py, so there's no external tool to
# fetch or set up.

# `python` on Windows, `python3` everywhere else. Override with `make PYTHON=...`
# if your system uses something different.
PYTHON ?= python3
ifeq (, $(shell command -v $(PYTHON) 2>/dev/null))
PYTHON := python
endif

SETUP := $(PYTHON) scripts/setup.py

.PHONY: help install cardconjurer node-install check clean-cc

help:
	@echo "mtg-proxies make targets:"
	@echo "  make install        full install: deps check + npm install + Card Conjurer clone + uv sync"
	@echo "  make cardconjurer   only lazy-clone the Card Conjurer source (~50 MB)"
	@echo "  make node-install   only run \`npm install\` in the harness dir"
	@echo "  make check          check that node/npm/git/uv are installed; report and exit"
	@echo "  make clean-cc       delete the cached Card Conjurer clone"
	@echo ""
	@echo "Don't have GNU make? Run \`python scripts/setup.py\` directly — same result."

install:
	$(SETUP)

cardconjurer:
	$(SETUP) --cardconjurer-only

node-install:
	$(SETUP) --node-only

check:
	$(SETUP) --check

clean-cc:
	$(SETUP) --clean-cardconjurer
