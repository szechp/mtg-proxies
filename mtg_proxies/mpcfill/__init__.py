"""MPCFill helpers: identifier-fetch and auto-matcher.

Modules:
  :mod:`search`     — search MPC Autofill by card name (``/2/exploreSearch/``)
  :mod:`cdn`        — fetch small CDN thumbnails (no auth, used for matching)
  :mod:`similarity` — Sobel-NCC image similarity scorer
  :mod:`automatch`  — combines search + CDN + similarity into one call
  :mod:`drive`      — fetch full-resolution renders from Google Drive
  :mod:`per_card`   — resolve a single ``#mpcfill --identifier`` modeline slot
  :mod:`cache`      — on-disk cache layout helpers
"""
