# mtg-proxies dev tasks. The Card Conjurer source isn't vendored — `make
# cardconjurer` fetches a pinned commit into ~/.cache/mtg-proxies/cardconjurer/
# the first time it's needed. `make install` runs everything for a fresh checkout.

CC_CACHE     := $(HOME)/.cache/mtg-proxies/cardconjurer
CC_REPO      := https://github.com/joshbirnholz/cardconjurer.git
CC_SHA       := $(shell cat cardconjurer.lock)
NODE_DIR     := mtg_proxies/cardconjourer/node

# Sparse-checkout pattern: only the engine code + fonts + the 8th-frame /
# manaSymbols / setSymbols assets we actually load. Keeps the cache to ~50 MB
# instead of the full ~4 GB upstream history.
CC_SPARSE := \
	/js/* \
	/fonts/* \
	/img/frames/* \
	/img/manaSymbols/* \
	/img/setSymbols/* \
	/img/*.png \
	/data/*

.PHONY: install cardconjurer clean-cc node-install

install: node-install cardconjurer
	uv sync

node-install:
	cd $(NODE_DIR) && npm install --silent

# Idempotent: clones once at the pinned SHA, exits 0 if already there.
cardconjurer:
	@if [ -d "$(CC_CACHE)/.git" ] && [ "$$(git -C $(CC_CACHE) rev-parse HEAD 2>/dev/null)" = "$(CC_SHA)" ]; then \
		echo "[cardconjurer] cache at $(CC_CACHE) is at the pinned SHA, skipping"; \
	else \
		echo "[cardconjurer] fetching $(CC_REPO) @ $(CC_SHA) → $(CC_CACHE) (partial + sparse)"; \
		mkdir -p $(CC_CACHE); \
		git clone --filter=blob:none --no-checkout $(CC_REPO) $(CC_CACHE) 2>/dev/null || true; \
		git -C $(CC_CACHE) sparse-checkout init --no-cone; \
		git -C $(CC_CACHE) sparse-checkout set $(CC_SPARSE); \
		git -C $(CC_CACHE) fetch --depth=1 origin $(CC_SHA); \
		git -C $(CC_CACHE) checkout $(CC_SHA); \
		echo "[cardconjurer] ready at $(CC_CACHE)"; \
	fi

clean-cc:
	rm -rf $(CC_CACHE)
