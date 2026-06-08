# Quobi brand assets

## Colors
| Token | Hex | Use |
| --- | --- | --- |
| Coral (gradient top) | `#ff7059` | Quill / icon gradient start |
| Coral (gradient bottom) | `#e23c2a` | Quill / icon gradient end |
| Coral (solid) | `#e8402c` | Wordmark "bi", accents, links |
| Ink | `#201d1a` | Wordmark "Quo", body text |
| Paper | `#f6f4ef` | App-icon tile, light backgrounds |
| Tile border | `#e7e3da` | Hairline around the paper tile |

## Typography
- Wordmark font: **Sora**, weight **700** (Bold), tracking ≈ **-1** (`-0.011em`).
- Two-tone wordmark: **Quo** in Ink + **bi** in Coral.
- Open Font License. In the lockup/wordmark SVGs the text is **outlined to paths**, so Sora is not required to render them.

## Files
**App icon** (off-white tile — use only as an app/launcher icon)
- `quobi-mark.svg` — master rounded squircle
- `quobi-mark-maskable.svg` — full-bleed for Android adaptive / PWA maskable
- `quobi-favicon.svg` — bolder feather tuned for tiny sizes

**Symbol** (no tile)
- `quobi-feather-coral.svg` — the coral quill, standalone
- `quobi-feather.svg` — single-color quill (`currentColor`), recolorable

**Wordmark & lockup** (outlined Sora)
- `quobi-wordmark.svg` / `quobi-wordmark-dark.svg`
- `quobi-lockup-horizontal.svg` — quill + wordmark (light backgrounds)
- `quobi-lockup-horizontal-dark.svg` — for dark backgrounds (off-white "Quo")

**exports/**
- `favicon.ico` (16/32/48), `favicon.svg`
- `png/` app-icon PNGs 16–1024, `apple-touch-icon.png`, `maskable-{192,512}.png`
- `lockup/`, `wordmark/` — transparent PNGs at 96/192/384px (light + dark)

## Usage notes
- The **off-white tile is for the app icon only.** For website headers / docs use the **glyph-only lockup** (`quobi-lockup-horizontal*.svg`).
- Don't add drop shadows, gloss, or recolor the quill outside the coral range.
- Rebuild the lockup with `python3 _build_lockup.py` (requires `fonttools`; edit `FS`, `LS`, `gap` there).

See `CREDITS.md` for the feather's Apache-2.0 attribution.
