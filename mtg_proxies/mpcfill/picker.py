"""Tkinter-based interactive picker for MPCFill candidates.

Used by the ``mtg-proxies mpcfill-pick`` subcommand. Opens a single blocking window
with every candidate render shown as a thumbnail grid, lets the user click one, and
returns the chosen :class:`Candidate`. Cancel or window-close returns ``None``.

This module is GUI-only and has no unit tests; the surrounding subcommand wiring
(``_run_mpcfill_pick`` in cli.py) is covered by the integration test suite.
"""

from __future__ import annotations

import io
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from mtg_proxies.mpcfill.types import Candidate


_PREVIEW_SIZE = 300  # px width of each thumbnail in the grid
_GRID_COLS = 4
_WINDOW_HEIGHT = 720


def pick_candidate_interactively(
    card_name: str,
    candidates: list[Candidate],
    drive_fetcher: Callable[[str, int], bytes],
    *,
    preview_size: int = _PREVIEW_SIZE,
    cols: int = _GRID_COLS,
) -> Candidate | None:
    """Open a Tkinter window showing every candidate as a clickable thumbnail grid.

    Args:
        card_name: Card name shown in the window title.
        candidates: Ordered list of backend Candidates to display.
        drive_fetcher: Callable ``(drive_id, size) -> bytes`` for preview thumbnails.
        preview_size: Width in pixels for each thumbnail (downloaded + displayed).
        cols: Number of grid columns.

    Returns:
        The Candidate the user clicked, or ``None`` if they closed/cancelled the window.
    """
    import tkinter as tk

    from PIL import Image, ImageTk

    if not candidates:
        return None

    # Pre-fetch previews in parallel. Even on a slow link, downloading the lot ahead of
    # window-open feels better than the window appearing empty and filling card-by-card.
    def _fetch(c: Candidate) -> tuple[Candidate, Image.Image | None]:
        try:
            data = drive_fetcher(c.drive_id, preview_size)
            with Image.open(io.BytesIO(data)) as raw:
                img = raw.convert("RGB").copy()
            # MTG cards are ~5:7 aspect; clamp box generously.
            img.thumbnail((preview_size, preview_size * 2))
        except Exception as exc:
            print(f"  warning: could not fetch preview for {c.drive_id}: {exc}")
            return c, None
        return c, img

    print(f"Fetching {len(candidates)} preview thumbnails...")
    with ThreadPoolExecutor(max_workers=8) as pool:
        fetched = list(pool.map(_fetch, candidates))
    thumbnails = [(c, img) for c, img in fetched if img is not None]
    if not thumbnails:
        return None

    selected: list[Candidate | None] = [None]

    root = tk.Tk()
    root.title(f"Pick MPCFill render for: {card_name}")

    # Scrollable canvas — a 50-candidate query won't fit on screen otherwise.
    canvas_w = cols * (preview_size + 30)
    canvas = tk.Canvas(root, width=canvas_w, height=_WINDOW_HEIGHT, highlightthickness=0)
    scrollbar = tk.Scrollbar(root, orient="vertical", command=canvas.yview)
    inner = tk.Frame(canvas)
    inner.bind("<Configure>", lambda _: canvas.configure(scrollregion=canvas.bbox("all")))
    canvas.create_window((0, 0), window=inner, anchor="nw")
    canvas.configure(yscrollcommand=scrollbar.set)
    canvas.pack(side="left", fill="both", expand=True)
    scrollbar.pack(side="right", fill="y")

    # Tk's PhotoImage objects are garbage-collected if not referenced from Python.
    photo_refs: list[ImageTk.PhotoImage] = []

    def _make_handler(c: Candidate) -> Callable[[], None]:
        def _handler() -> None:
            selected[0] = c
            root.destroy()

        return _handler

    for idx, (candidate, img) in enumerate(thumbnails):
        row, col = divmod(idx, cols)
        cell = tk.Frame(inner, padx=6, pady=6, borderwidth=1, relief="solid")
        cell.grid(row=row, column=col, sticky="n")
        photo = ImageTk.PhotoImage(img)
        photo_refs.append(photo)
        tk.Button(cell, image=photo, command=_make_handler(candidate)).pack()
        info = f"{candidate.source_name}\nDPI: {candidate.dpi or '?'}\nIdentifier: {candidate.drive_id}"
        tk.Label(cell, text=info, justify="left", font=("Helvetica", 9), wraplength=preview_size).pack(anchor="w")

    # Mouse-wheel scrolling — Tkinter needs platform-specific bindings.
    def _on_mousewheel(event: tk.Event) -> None:
        canvas.yview_scroll(int(-event.delta / 120), "units")

    canvas.bind_all("<MouseWheel>", _on_mousewheel)
    canvas.bind_all("<Button-4>", lambda _: canvas.yview_scroll(-1, "units"))  # Linux
    canvas.bind_all("<Button-5>", lambda _: canvas.yview_scroll(1, "units"))  # Linux

    root.mainloop()
    return selected[0]
