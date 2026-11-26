#!/usr/bin/env python3
import argparse
import copy
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET

import numpy as np
from PIL import Image

SVG_NS = "http://www.w3.org/2000/svg"
ET.register_namespace("", SVG_NS)
ET.register_namespace("xlink", "http://www.w3.org/1999/xlink")
NUM = re.compile(r"[-+]?(?:\d*\.\d+|\d+\.?)(?:[eE][-+]?\d+)?")
SHAPES = {"rect", "circle", "ellipse", "line", "polyline", "polygon", "path", "image", "use"}
HOLDERS = {"defs", "clipPath", "mask", "pattern", "filter", "marker", "symbol"}
LENGTH = re.compile(r"\s*([-+]?(?:\d*\.\d+|\d+\.?)(?:[eE][-+]?\d+)?)\s*(px|pt|pc|in|cm|mm|q|em|ex)?\s*$", re.IGNORECASE)
UNITS = {"px": 1, "pt": 4 / 3, "pc": 16, "in": 96, "cm": 96 / 2.54, "mm": 96 / 25.4, "q": 96 / 101.6, "em": 16, "ex": 8}
ALIGN = {"Min": 0.0, "Mid": 0.5, "Max": 1.0}


def tag(el):
    return el.tag.split("}")[-1]


def cairo_dir():
    if os.name != "nt":
        return None
    path = [d for d in os.environ.get("PATH", "").split(os.pathsep) if d]
    if any(os.path.isfile(os.path.join(d, "libcairo-2.dll")) for d in path):
        return None
    pf = os.environ.get("ProgramFiles", r"C:\Program Files")
    apps = os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs")
    dirs = os.environ.get("CAIROCFFI_DLL_DIRECTORIES", "").split(";") + [
        r"C:\msys64\ucrt64\bin", r"C:\msys64\mingw64\bin",
        os.path.join(pf, "GTK3-Runtime Win64", "bin"), os.path.join(pf, "Inkscape", "bin"),
        os.path.join(pf, "Siril", "bin"), os.path.join(pf, "GIMP 3", "bin"),
        os.path.join(apps, "GIMP 3", "bin"), os.path.join(pf, "GIMP 2", "bin"),
        os.path.join(apps, "GIMP 2", "bin"),
    ]
    return next((d for d in dirs if d and os.path.isfile(os.path.join(d, "libcairo-2.dll"))), None)


def render(svg_path, png_path, width):
    if shutil.which("rsvg-convert"):
        done = subprocess.run(["rsvg-convert", "-w", str(width), svg_path, "-o", png_path], check=False, capture_output=True)
        if done.returncode:
            err = " ".join(done.stderr.decode("utf-8", "replace").split())
            sys.exit(f"rsvg-convert could not render the SVG: {err[:300] or 'exit code ' + str(done.returncode)}")
        return
    found = cairo_dir()
    if found:
        os.environ["PATH"] = found + os.pathsep + os.environ.get("PATH", "")
    try:
        import cairosvg
    except (ImportError, OSError):
        sys.exit("needs rsvg-convert, or the cairosvg python package with a cairo library it can load")
    tree = read_svg(svg_path)
    for_cairo(tree.getroot())
    try:
        cairosvg.svg2png(bytestring=ET.tostring(tree.getroot()), url=svg_path, write_to=png_path, output_width=width)
    except (ValueError, TypeError, LookupError, ArithmeticError, SyntaxError, OSError, MemoryError) as e:
        sys.exit(f"cairosvg could not render the SVG: {' '.join(str(e).split())[:300] or type(e).__name__}")


def style_value(el, prop):
    value = el.get(prop)
    for decl in el.get("style", "").split(";"):
        k, _, v = decl.partition(":")
        if k.strip() == prop:
            value = v.strip()
    return value


def add_style(el, decl):
    st = el.get("style", "")
    el.set("style", (st + ";" if st else "") + decl)


def stroke_first(order):
    words = [w for w in (order or "").split() if w in ("fill", "stroke", "markers")]
    words += [w for w in ("fill", "stroke", "markers") if w not in words]
    return words.index("stroke") < words.index("fill")


def for_cairo(root):
    if style_value(root, "text-rendering") is None:
        root.set("text-rendering", "optimizeSpeed")
    parent = {c: p for p in root.iter() for c in p}
    for t in texts(root):
        el, order = t, None
        while el is not None and order is None:
            order = style_value(el, "paint-order")
            el = parent.get(el)
        if not stroke_first(order):
            continue
        under = copy.deepcopy(t)
        for e in under.iter():
            e.attrib.pop("id", None)
            add_style(e, "fill:none")
        for e in t.iter():
            add_style(e, "stroke:none")
        up = parent[t]
        up.insert(list(up).index(t), under)


def flat(png, bg):
    im = np.asarray(Image.open(png).convert("RGBA"), dtype=np.float32)
    a = im[:, :, 3:4] / 255
    return im[:, :, :3] * a + bg * (1 - a)


def length(value):
    m = LENGTH.match(value or "")
    if not m:
        return None
    n = float(m.group(1)) * UNITS[(m.group(2) or "px").lower()]
    return n if 0 < n < math.inf else None


def view_box(root):
    vb = [float(n) for n in NUM.findall(root.get("viewBox", ""))]
    if len(vb) != 4:
        return None
    if not all(math.isfinite(v) for v in vb) or vb[2] <= 0 or vb[3] <= 0:
        sys.exit("the viewBox needs a width and height above zero")
    return vb


def renders_as_is(root):
    wv, hv = root.get("width"), root.get("height")
    if length(wv) and length(hv):
        return True
    return view_box(root) is not None and all(v is None or v.strip().endswith("%") for v in (wv, hv))


def canvas(root):
    vb = view_box(root)
    w, h = length(root.get("width")), length(root.get("height"))
    if vb is None:
        if w is None or h is None:
            sys.exit('the SVG has no viewBox and no width and height, so its canvas size is unknown; add viewBox="0 0 W H"')
        return w, h, [0.0, 0.0, w, h]
    if w is None and h is None:
        w, h = vb[2], vb[3]
    elif w is None:
        w = h * vb[2] / vb[3]
    elif h is None:
        h = w * vb[3] / vb[2]
    words = root.get("preserveAspectRatio", "").replace("defer", "").split()
    if words[:1] == ["none"]:
        return w, h, vb
    s = (max if "slice" in words[1:] else min)(w / vb[2], h / vb[3])
    m = re.fullmatch(r"x(Min|Mid|Max)Y(Min|Mid|Max)", words[0] if words else "")
    ax, ay = (ALIGN[m.group(1)], ALIGN[m.group(2)]) if m else (0.5, 0.5)
    return w, h, [vb[0] + (vb[2] - w / s) * ax, vb[1] + (vb[3] - h / s) * ay, w / s, h / s]


def texts(root):
    out = []

    def walk(el, inside_holder):
        for c in el:
            t = tag(c)
            if t in HOLDERS:
                walk(c, True)
            elif t == "text" and not inside_holder:
                out.append(c)
            else:
                walk(c, inside_holder)

    walk(root, False)
    return out


def label(el):
    s = " ".join("".join(el.itertext()).split())
    return s[:40] if s else "(empty)"


def hide(el):
    st = el.get("style", "")
    el.set("style", (st + ";" if st else "") + "display:none")


def drop(el, prop):
    el.attrib.pop(prop, None)
    st = el.get("style", "")
    if prop in st:
        el.set("style", re.sub(prop + r"\s*:[^;]*;?", "", st))


def strip(el, solo):
    for c in el:
        if tag(c) in HOLDERS:
            continue
        if solo:
            if tag(c) in SHAPES:
                hide(c)
            drop(c, "clip-path")
        drop(c, "filter")
        strip(c, solo)


def write(tree, path):
    tree.write(path, encoding="unicode")


KANA = re.compile(r"[\u3040-\u30ff\u31f0-\u31ff\uff65-\uff9f]")
HANGUL = re.compile(r"[\u1100-\u11ff\u3130-\u318f\uac00-\ud7af\uffa0-\uffdc]")
HAN = re.compile(r"[\u3000-\u303f\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\uff00-\uff64]")
CJK_FONT = {"Japanese": "Noto Sans CJK JP", "Korean": "Noto Sans CJK KR", "Chinese": "Noto Sans CJK SC"}


def cjk_fonts_missing(root):
    text = "".join("".join(el.itertext()) for el in root.iter() if tag(el) == "text")
    found = {name: rx.search(text) for name, rx in (("kana", KANA), ("hangul", HANGUL), ("han", HAN))}
    if not any(found.values()) or not shutil.which("fc-list"):
        return []
    lang = "Japanese" if found["kana"] else "Korean" if found["hangul"] else "Chinese"
    langs = {"kana": "Japanese", "hangul": "Korean", "han": lang}
    missing = []
    for kind, m in found.items():
        if not m or langs[kind] in missing:
            continue
        fonts = subprocess.run(["fc-list", f":charset={ord(m.group()):x}", "family"], check=False, capture_output=True, text=True).stdout
        if not fonts.strip():
            missing.append(langs[kind])
    return missing


def bounded(kind, low, high):
    def parse(text):
        try:
            value = kind(text)
        except ValueError:
            raise argparse.ArgumentTypeError(f"not a number: {text}") from None
        if not low <= value <= high:
            raise argparse.ArgumentTypeError(f"must be from {low:g} to {high:g}")
        return value

    return parse


def read_svg(path):
    try:
        with open(path, "rb") as f:
            data = f.read()
    except OSError as e:
        sys.exit(f"cannot open {path}: {e.strerror}")
    if data[:2] == b"\x1f\x8b":
        sys.exit(f"{path} is gzip-compressed (svgz); unzip it to a plain .svg first")
    try:
        return ET.ElementTree(ET.fromstring(data))
    except ET.ParseError as e:
        sys.exit(f"{path} is not valid SVG XML: {e}")


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser()
    ap.add_argument("svg")
    ap.add_argument("--out", default=None)
    ap.add_argument("--width", type=bounded(int, 100, 4000), default=800)
    ap.add_argument("--small", type=bounded(int, 8, 1024), default=64)
    ap.add_argument("--min-text", type=bounded(float, 0, 100), default=1.2, help="smallest text height as a percent of the image side; about 0.6 for interfaces")
    args = ap.parse_args()
    tmp = tempfile.mkdtemp()
    try:
        check(args, tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def check(args, tmp):
    tree = read_svg(args.svg)
    root = tree.getroot()
    vw, vh, (x0, y0, w, h) = canvas(root)
    full_h = args.width * vh / vw
    sx, sy = args.width / w, full_h / h
    p = round(0.25 * max(args.width, full_h))
    if (args.width + 2 * p) * (full_h + 2 * p) > Image.MAX_IMAGE_PIXELS:
        sys.exit(f"the image is too tall to check at --width {args.width}; use a smaller --width")
    src = args.svg
    if not renders_as_is(root):
        root.set("width", str(vw))
        root.set("height", str(vh))
        src = os.path.join(tmp, "sized.svg")
        write(tree, src)

    name = os.path.splitext(os.path.basename(args.svg))[0]
    if args.out:
        out = args.out
        os.makedirs(out, exist_ok=True)
    else:
        base = os.path.join(tempfile.gettempdir(), "antialgorithm_check")
        os.makedirs(base, exist_ok=True)
        out = tempfile.mkdtemp(prefix=name + "-", dir=base)
    full_png = os.path.join(out, "full.png")
    small_png = os.path.join(out, "small.png")
    render(src, full_png, args.width)
    render(src, small_png, args.small)
    full = flat(full_png, 255)
    rows, cols = full.shape[:2]
    if cols != args.width or abs(rows - full_h) > 1:
        sys.exit(f"the renderer drew {cols}x{rows} px, not {args.width}x{full_h:.0f}; check the width, height and viewBox of the svg element")
    clear = bool((np.asarray(Image.open(full_png).convert("RGBA"))[:int(full_h + 1e-6), :, 3] < 250).any())
    full_dark = flat(full_png, 0) if clear else None

    parent = {c: p for p in root.iter() for c in p}

    def has_filter(el):
        return "filter" in el.attrib or "filter" in el.get("style", "")

    def filtered(el):
        while el is not None:
            if has_filter(el):
                return True
            el = parent.get(el)
        return False

    text_filtered = [filtered(t) for t in texts(root)]
    plain = None
    if any(text_filtered):
        nf = copy.deepcopy(tree)
        strip(nf.getroot(), False)
        nf_svg = os.path.join(tmp, "nofilter.svg")
        nf_png = os.path.join(tmp, "nofilter.png")
        write(nf, nf_svg)
        render(nf_svg, nf_png, args.width)
        plain = flat(nf_png, 255)

    problems = []
    for lang in cjk_fonts_missing(root):
        problems.append(f"{lang} text found but no installed font has its characters, so it may render as empty boxes (install {CJK_FONT[lang]})")
    n = len(texts(root))
    for i in range(n):
        solo = copy.deepcopy(tree)
        sroot = solo.getroot()
        strip(sroot, True)
        for j, t in enumerate(texts(sroot)):
            if j != i:
                hide(t)
        sroot.set("viewBox", f"{x0 - p / sx} {y0 - p / sy} {(args.width + 2 * p) / sx} {(rows + 2 * p) / sy}")
        sroot.set("width", str(args.width + 2 * p))
        sroot.set("height", str(rows + 2 * p))
        sroot.set("preserveAspectRatio", "none")
        sp = os.path.join(tmp, f"s{i}.svg")
        write(solo, sp)
        pp = os.path.join(tmp, f"s{i}.png")
        render(sp, pp, args.width + 2 * p)
        a = np.asarray(Image.open(pp).convert("RGBA"))[:, :, 3]
        ys, xs = np.nonzero(a > 40)
        name = label(texts(root)[i])
        if len(xs) == 0:
            problems.append(f"text '{name}': draws nothing")
            continue
        cw, ch = cols, rows
        sides = []
        if xs.min() < p:
            sides.append("left")
        if xs.max() >= p + cw:
            sides.append("right")
        if ys.min() < p:
            sides.append("top")
        if ys.max() >= p + ch:
            sides.append("bottom")
        if sides:
            problems.append(f"text '{name}': runs off the {'/'.join(sides)} edge")
        lines = max(1, len(texts(root)[i].findall(f"{{{SVG_NS}}}tspan")))
        share = (ys.max() - ys.min() + 1) / lines / max(args.width, full_h)
        if share * 100 < args.min_text and not sides:
            problems.append(f"text '{name}': very small, about {share * 100:.1f}% of the image side (want {args.min_text:g}%+)")

        rgba = np.asarray(Image.open(pp).convert("RGBA"), dtype=np.float32)
        inner = rgba[p:p + ch, p:p + cw]
        ih, iw = min(inner.shape[0], ch), min(inner.shape[1], cw)
        mask = np.zeros(full.shape[:2], bool)
        mask[:ih, :iw] = inner[:ih, :iw, 3] > 200
        if mask.sum() < 4:
            continue
        own = np.zeros_like(full)
        own[:ih, :iw] = inner[:ih, :iw, :3]
        base = plain if text_filtered[i] else full
        cover = (np.abs(base - own).mean(axis=2)[mask] > 60).mean()
        ring = np.zeros_like(mask)
        r = max(2, int(3 * sx))
        for dy in (-r, 0, r):
            for dx in (-r, 0, r):
                ring |= np.roll(np.roll(mask, dy, 0), dx, 1)
        ring &= ~mask
        lum = lambda c: c @ np.array([0.2126, 0.7152, 0.0722], dtype=np.float32)
        contrast = 255
        if ring.any():
            contrast = max(abs(lum(img[mask]).mean() - lum(img[ring]).mean()) for img in (full, full_dark) if img is not None)
        if cover > 0.04:
            problems.append(f"text '{name}': {cover * 100:.0f}% covered or changed by something drawn over it")
        elif contrast < 30:
            problems.append(f"text '{name}': low contrast with what is behind it ({contrast:.0f}, want 30+)")

    print(f"render: {full_png}")
    print(f"small {args.small}px: {small_png}")
    print(f"texts checked: {n}")
    if clear:
        print("background has see-through parts; contrast was measured on white and on black, the better one counts")
    if problems:
        print("problems:")
        for pr in problems:
            print(f"  - {pr}")
        sys.exit(1)
    print("no text problems found. Now look at both renders yourself.")


if __name__ == "__main__":
    try:
        main()
    except RecursionError:
        sys.exit("the SVG nests its elements too deeply to check")
