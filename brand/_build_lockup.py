#!/usr/bin/env python3
"""Build Quobi horizontal lockup: coral feather glyph + outlined Sora 'Quobi' wordmark."""
from fontTools.ttLib import TTFont
from fontTools.varLib.instancer import instantiateVariableFont
from fontTools.pens.svgPathPen import SVGPathPen
from fontTools.pens.boundsPen import BoundsPen

SRC = "Sora-VF.ttf"
OUT = "."

FEATHER = ("M22 2s-7.64-.37-13.66 7.88C3.72 16.21 2 22 2 22l1.94-1c1.44-2.5 2.19-3.53 "
           "3.6-5c2.53.74 5.17.65 7.46-2c-2-.56-3.6-.43-5.96-.19C11.69 12 13.5 11.6 16 12l1-2"
           "c-1.8-.34-3-.37-4.78.04C14.19 8.65 15.56 7.87 18 8l1.21-1.93c-1.56-.11-2.5.06-4.29.5"
           "c1.61-1.46 3.08-2.12 5.22-2.25c0 0 1.05-1.89 1.86-2.32")

INK = "#201d1a"
CORAL = "#e8402c"
OFFWHITE = "#f6f4ef"

f = TTFont(SRC)
instantiateVariableFont(f, {"wght": 700}, inplace=True)
upem = f["head"].unitsPerEm
cmap = f.getBestCmap()
gs = f.getGlyphSet()
hmtx = f["hmtx"]

FS = 160.0
scale = FS / upem
LS = -0.011 * FS          # letter-spacing in px (~tracking -1)
text = "Quobi"
split = 3                  # "Quo" | "bi"

glyphs = []                # (path_d, x_px, fill_index)
penx = 0.0
allxmin = allxmax = None
ymax_cap = None            # cap top (use 'Q')
ymin_all = 0.0
for i, ch in enumerate(text):
    gname = cmap[ord(ch)]
    sp = SVGPathPen(gs)
    gs[gname].draw(sp)
    d = sp.getCommands()
    bp = BoundsPen(gs)
    gs[gname].draw(bp)
    adv = hmtx[gname][0] * scale
    if bp.bounds:
        xmin, ymin, xmax, ymax = bp.bounds
        gx0 = penx + xmin * scale
        gx1 = penx + xmax * scale
        allxmin = gx0 if allxmin is None else min(allxmin, gx0)
        allxmax = gx1 if allxmax is None else max(allxmax, gx1)
        ymax_cap = ymax if ymax_cap is None else max(ymax_cap, ymax)
        ymin_all = min(ymin_all, ymin)
    glyphs.append((d, penx, 0 if i < split else 1))
    penx += adv + LS

cap_px = ymax_cap * scale          # baseline -> cap top
desc_px = -ymin_all * scale        # baseline -> lowest descender
word_w = allxmax - allxmin
# normalize so wordmark local x starts at 0
x_shift = -allxmin

def glyph_paths(fills):
    out = []
    for d, gx, fi in glyphs:
        out.append(f'    <path transform="translate({gx + x_shift:.2f} 0) scale({scale:.5f} {-scale:.5f})" '
                   f'fill="{fills[fi]}" d="{d}"/>')
    return "\n".join(out)

# ---- wordmark-only SVG (baseline placed at y=cap_px, with small pad) ----
pad = 12
W_wm = word_w + 2 * pad
H_wm = cap_px + desc_px + 2 * pad
baseline_wm = pad + cap_px
def wordmark_svg(fills, bg=None):
    rect = f'<rect width="{W_wm:.1f}" height="{H_wm:.1f}" fill="{bg}"/>' if bg else ""
    body = glyph_paths(fills).replace('translate(', f'__T__')  # placeholder
    # rebuild with baseline offset
    rows = []
    for d, gx, fi in glyphs:
        rows.append(f'  <path transform="translate({gx + x_shift + pad:.2f} {baseline_wm:.2f}) '
                    f'scale({scale:.5f} {-scale:.5f})" fill="{fills[fi]}" d="{d}"/>')
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{W_wm:.0f}" height="{H_wm:.0f}" '
            f'viewBox="0 0 {W_wm:.1f} {H_wm:.1f}">{rect}\n' + "\n".join(rows) + "\n</svg>\n")

with open(f"{OUT}/quobi-wordmark.svg", "w") as fp:
    fp.write(wordmark_svg([INK, CORAL]))
with open(f"{OUT}/quobi-wordmark-dark.svg", "w") as fp:
    fp.write(wordmark_svg([OFFWHITE, CORAL]))

# ---- lockup: feather + wordmark ----
PAD = 18
B = PAD + cap_px               # baseline
# Align the quill to the 'Q' optical centre. The Q has a descender tail, so its
# true centre sits BELOW the cap-centre — centring on cap height made it float high.
qbp = BoundsPen(gs); gs[cmap[ord('Q')]].draw(qbp)
qxmin, qymin, qxmax, qymax = qbp.bounds
q_top = B - qymax * scale      # top of Q (cap line)
q_bot = B - qymin * scale      # bottom of Q tail
feather_h = (q_bot - q_top) * 1.12
fscale = feather_h / 24.0
feather_w = 24 * fscale
# The feather path occupies x in ~[2,22] of its 24u box; cancel that padding so the
# spacing is measured edge-to-edge, not box-to-box.
fpad = 2 * fscale
gap = cap_px * 0.10
feather_x = PAD - fpad                         # feather's visual left edge sits at PAD
feather_cy = (q_top + q_bot) / 2.0             # centre on the Q
feather_y = feather_cy - feather_h / 2.0
word_x = PAD + (feather_w - 2 * fpad) + gap    # word starts just past the feather's visual right edge
total_w = word_x + word_w + PAD
total_h = max(B + desc_px, feather_y + feather_h) + PAD

def lockup_svg(fills, bg=None):
    rect = f'<rect width="{total_w:.1f}" height="{total_h:.1f}" fill="{bg}"/>' if bg else ""
    rows = [f'  <g transform="translate({feather_x:.2f} {feather_y:.2f}) scale({fscale:.4f})" fill="url(#coral)">',
            f'    <path d="{FEATHER}"/>', '  </g>']
    for d, gx, fi in glyphs:
        rows.append(f'  <path transform="translate({word_x + gx + x_shift:.2f} {B:.2f}) '
                    f'scale({scale:.5f} {-scale:.5f})" fill="{fills[fi]}" d="{d}"/>')
    defs = ('<defs><linearGradient id="coral" x1="0.15" y1="0" x2="0.85" y2="1">'
            '<stop offset="0" stop-color="#ff7059"/><stop offset="1" stop-color="#e23c2a"/>'
            '</linearGradient></defs>')
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{total_w:.0f}" height="{total_h:.0f}" '
            f'viewBox="0 0 {total_w:.1f} {total_h:.1f}">{defs}{rect}\n' + "\n".join(rows) + "\n</svg>\n")

with open(f"{OUT}/quobi-lockup-horizontal.svg", "w") as fp:
    fp.write(lockup_svg([INK, CORAL]))
with open(f"{OUT}/quobi-lockup-horizontal-dark.svg", "w") as fp:
    fp.write(lockup_svg([OFFWHITE, CORAL]))

print(f"upem={upem} scale={scale:.4f} cap_px={cap_px:.1f} desc_px={desc_px:.1f} word_w={word_w:.1f}")
print(f"lockup {total_w:.0f}x{total_h:.0f}  feather {feather_w:.0f}x{feather_h:.0f}  gap={gap:.0f}")
print("wrote: quobi-wordmark[.|-dark].svg, quobi-lockup-horizontal[.|-dark].svg")
