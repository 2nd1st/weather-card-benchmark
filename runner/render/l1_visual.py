"""L1 single-card descriptive scalars (scheme §3 + appendix A "L1 视觉标量公式").

The frozen main screenshot (``shot.png``) → the ``l1`` object of ``meta.json``:

  * ``bytes``      — byte-size split of the card document (dev field split).
  * ``structure``  — DOM/CSS/JS structural counts.
  * ``visual``     — colorfulness / brightness / contrast / whitespace_ratio /
                     frame_change (appendix A formulas, verbatim).
  * ``palette_top8`` — top-8 Lab-bin swatches (reuses ``v_palette``).

REUSE (task W): the pixel front-end (alpha→white composite, LANCZOS 128×80) is the
SAME preprocessing as the sibling ``v-color`` channel, and the palette histogram +
top-8 swatch come straight from ``v_palette`` — this module only adds the appendix-A
scalar formulas and the §2.3 frame-change reducer.

Appendix A "L1 视觉标量公式" (input 128×80 float64 unless noted):

  colorfulness | Hasler–Süsstrunk: rg=R−G, yb=(R+G)/2−B;
                 M=√(σ²_rg+σ²_yb)+0.3·√(μ²_rg+μ²_yb)  (σ = population std, ddof=0)
  brightness   | mean(Rec.601 gray), [0,255]
  contrast     | gray population std (ddof=0)
  whitespace   | v-color 同款 64-bin 量化后最频 bin(并列取 bin 索引最小)的像素占比
  frame-change | §2.3: 相邻虚拟帧逐像素变化(任一通道差>0 计变)占比的 median/max
"""

from __future__ import annotations

import io
from html.parser import HTMLParser
from typing import Any, Optional

import numpy as np
from PIL import Image

from ..similarity import v_palette
from ..similarity.v_color import _composite_white  # reuse: alpha→white composite

# Shared L1 visual input size (appendix A header "输入 128×80"; matches v-color).
RESIZE_WH = (128, 80)  # (width, height) for PIL.resize
# v-color 64-bin quantization constants (appendix A v-color row).
_CHANNEL_SHIFT = 6
_LEVELS = 4
_NBINS = _LEVELS ** 3  # 64
# Rec.601 luma weights (appendix A imaging: 灰度=Rec.601).
_REC601 = np.array([0.299, 0.587, 0.114], dtype=np.float64)


# --------------------------------------------------------------------------- #
# bytes + structure (DOM/CSS/JS counts) — moved here so run_batch + the offline
# re-render driver share one source (scheme §3 体积/结构标量).
# --------------------------------------------------------------------------- #
class _DomCounter(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.nodes = 0
        self.depth = 0
        self.max_depth = 0
        self._void = {
            "area", "base", "br", "col", "embed", "hr", "img", "input", "link",
            "meta", "param", "source", "track", "wbr",
        }
        self._in_style = 0
        self._in_script = 0
        self.style_text: list[str] = []
        self.script_text: list[str] = []

    def handle_starttag(self, tag, attrs):
        self.nodes += 1
        if tag == "style":
            self._in_style += 1
        if tag == "script":
            self._in_script += 1
        if tag not in self._void:
            self.depth += 1
            self.max_depth = max(self.max_depth, self.depth)

    def handle_startendtag(self, tag, attrs):
        self.nodes += 1

    def handle_endtag(self, tag):
        if tag == "style":
            self._in_style = max(0, self._in_style - 1)
        if tag == "script":
            self._in_script = max(0, self._in_script - 1)
        if tag not in self._void and self.depth > 0:
            self.depth -= 1

    def handle_data(self, data):
        if self._in_style:
            self.style_text.append(data)
        if self._in_script:
            self.script_text.append(data)


def l1_bytes_structure(html: str) -> dict[str, Any]:
    """Byte-size split + DOM/CSS/JS structural counts (scheme §3 体积/结构标量).

    The css/js byte split (text inside ``<style>`` / ``<script>``) is a dev
    approximation of the frozen appendix-A definition — FLAGGED, unchanged from
    the prior run_batch behavior."""
    total = len(html.encode("utf-8"))
    p = _DomCounter()
    p.feed(html)
    css_text = "".join(p.style_text)
    js_text = "".join(p.script_text)
    css_bytes = len(css_text.encode("utf-8"))
    js_bytes = len(js_text.encode("utf-8"))
    html_bytes = max(0, total - css_bytes - js_bytes)
    css_rules = css_text.count("{")  # rough dev approximation
    return {
        "bytes": {"total": total, "html": html_bytes, "css": css_bytes, "js": js_bytes},
        "structure": {
            "dom_nodes": p.nodes,
            "css_rules": css_rules,
            "js_bytes_present": js_bytes > 0,
            "dom_depth": p.max_depth,
        },
    }


# --------------------------------------------------------------------------- #
# pixel front-end (reuse v-color preprocessing)
# --------------------------------------------------------------------------- #
def _rgb128x80(png_bytes: bytes) -> np.ndarray:
    """PNG bytes → (80, 128, 3) uint8 RGB: alpha→white composite, LANCZOS 128×80.

    Identical preprocessing to the v-color channel (appendix A imaging)."""
    with Image.open(io.BytesIO(png_bytes)) as im:
        rgb = _composite_white(im)
        resized = rgb.resize(RESIZE_WH, Image.LANCZOS)
        return np.asarray(resized, dtype=np.uint8)


# --------------------------------------------------------------------------- #
# visual scalars (appendix A formulas — BYTE-EXACT, never simplify)
# --------------------------------------------------------------------------- #
def colorfulness(arr_f64: np.ndarray) -> float:
    """Hasler–Süsstrunk colorfulness on a float64 [0,255] RGB array."""
    r = arr_f64[..., 0]
    g = arr_f64[..., 1]
    b = arr_f64[..., 2]
    rg = r - g
    yb = (r + g) / 2.0 - b
    var_rg = float(np.var(rg))  # ddof=0 (population)
    var_yb = float(np.var(yb))
    mean_rg = float(np.mean(rg))
    mean_yb = float(np.mean(yb))
    std_root = np.sqrt(var_rg + var_yb)
    mean_root = np.sqrt(mean_rg * mean_rg + mean_yb * mean_yb)
    return float(std_root + 0.3 * mean_root)


def _gray601(arr_f64: np.ndarray) -> np.ndarray:
    return arr_f64 @ _REC601


def brightness(arr_f64: np.ndarray) -> float:
    """mean(Rec.601 gray), [0,255]."""
    return float(np.mean(_gray601(arr_f64)))


def contrast(arr_f64: np.ndarray) -> float:
    """gray population std (ddof=0)."""
    return float(np.std(_gray601(arr_f64)))


def whitespace_ratio(arr_u8: np.ndarray) -> float:
    """v-color 64-bin quantized most-frequent-bin pixel proportion.

    ``>>6`` → 4 levels, C-order index r*16+g*4+b, most-frequent bin (ties → the
    smallest bin index via argmax's first-max semantics), proportion of pixels."""
    q = (arr_u8 >> _CHANNEL_SHIFT).astype(np.int64)  # {0,1,2,3}
    idx = q[..., 0] * (_LEVELS * _LEVELS) + q[..., 1] * _LEVELS + q[..., 2]
    counts = np.bincount(idx.ravel(), minlength=_NBINS)
    total = int(counts.sum())
    if total <= 0:
        return 0.0
    return float(int(counts.max()) / total)


# --------------------------------------------------------------------------- #
# frame-change (§2.3) — median/max adjacent per-pixel change ratio
# --------------------------------------------------------------------------- #
def _decode_rgb(png_bytes: bytes) -> np.ndarray:
    with Image.open(io.BytesIO(png_bytes)) as im:
        return np.asarray(_composite_white(im), dtype=np.uint8)


def frame_change(frame_pngs: list[bytes]) -> dict[str, float]:
    """Adjacent virtual-frame per-pixel change ratio (any channel diff>0),
    median/max (scheme §2.3, appendix A frame-change).

    ``frame_pngs`` = the §2.3 sampled frames (t_v+{700,1300,2100,3400}); N frames
    → N−1 adjacent-pair ratios. Fewer than 2 frames → {0.0, 0.0} (no motion
    observable)."""
    if not frame_pngs or len(frame_pngs) < 2:
        return {"median": 0.0, "max": 0.0}
    frames = [_decode_rgb(p) for p in frame_pngs]
    ratios: list[float] = []
    for a, b in zip(frames[:-1], frames[1:]):
        if a.shape != b.shape:
            # Shape drift (should not happen with a fixed viewport) → count as full
            # change for that pair, conservatively.
            ratios.append(1.0)
            continue
        changed = np.any(a.astype(np.int16) != b.astype(np.int16), axis=-1)
        ratios.append(float(np.count_nonzero(changed) / changed.size))
    arr = np.array(ratios, dtype=np.float64)
    return {"median": float(np.median(arr)), "max": float(np.max(arr))}


# --------------------------------------------------------------------------- #
# palette top-8 (reuse v_palette) → slot-meta shape
# --------------------------------------------------------------------------- #
def palette_top8(png_bytes: bytes) -> list[dict[str, Any]]:
    """Top-8 Lab-bin swatches in the slot-meta ``palette_top8`` shape
    ({bin_index, weight, lab:{L,a,b}}). Reuses ``v_palette`` histogram + swatch."""
    with Image.open(io.BytesIO(png_bytes)) as im:
        rgb = v_palette._composite_and_resize(im)
    hist = v_palette._histogram_from_rgb(rgb)
    if hist is None:
        return []
    out: list[dict[str, Any]] = []
    for sw in v_palette.top8_swatches(hist):
        cl, ca, cb = sw["lab_center"]
        out.append({
            "bin_index": int(sw["bin"]),
            "weight": float(sw["weight"]),
            "lab": {"L": float(cl), "a": float(ca), "b": float(cb)},
        })
    return out


def visual_scalars(main_png: bytes, frame_pngs: Optional[list[bytes]] = None) -> dict[str, Any]:
    """The ``visual`` sub-object of l1 from the frozen main PNG + §2.3 frames."""
    arr_u8 = _rgb128x80(main_png)
    arr_f64 = arr_u8.astype(np.float64)
    return {
        "colorfulness": colorfulness(arr_f64),
        "brightness": brightness(arr_f64),
        "contrast": contrast(arr_f64),
        "whitespace_ratio": whitespace_ratio(arr_u8),
        "frame_change": frame_change(frame_pngs or []),
    }


def compute_l1(html: str, main_png: bytes, frame_pngs: Optional[list[bytes]] = None) -> dict[str, Any]:
    """Full l1 object (bytes + structure + visual + palette_top8) — slot-meta shape."""
    l1 = l1_bytes_structure(html)
    l1["visual"] = visual_scalars(main_png, frame_pngs)
    l1["palette_top8"] = palette_top8(main_png)
    return l1
