#!/usr/bin/env python3
import argparse
import math
import random
import re
import xml.etree.ElementTree as ET

SVG_NS = "http://www.w3.org/2000/svg"
ET.register_namespace("", SVG_NS)
ET.register_namespace("xlink", "http://www.w3.org/1999/xlink")

MAX_DEPTH = 500
SHAPE_ATTRS = ("x", "y", "width", "height", "x1", "y1", "x2", "y2", "points", "cx", "cy", "r", "rx", "ry")
SKIP = {"defs", "clipPath", "mask", "pattern", "filter", "marker", "symbol", "linearGradient", "radialGradient", "style", "title", "desc"}
NUM = re.compile(r"[-+]?(?:\d*\.\d+|\d+\.?)(?:[eE][-+]?\d+)?")
TOK = re.compile(r"[MmLlHhVvCcSsQqTtAaZz]|[-+]?(?:\d*\.\d+|\d+\.?)(?:[eE][-+]?\d+)?")
LENGTH = re.compile(r"\s*([-+]?(?:\d*\.\d+|\d+\.?)(?:[eE][-+]?\d+)?)\s*([a-zA-Z%]*)\s*$")
UNIT_PX = {"": 1.0, "px": 1.0, "pt": 4 / 3, "pc": 16.0, "mm": 96 / 25.4, "cm": 96 / 2.54, "in": 96.0, "q": 96 / 101.6, "rem": 16.0}
TRANSFORM = re.compile(r"(matrix|translate|scale|rotate|skewX|skewY)\s*\(([^)]*)\)")
ARGS = {"m": 2, "l": 2, "h": 1, "v": 1, "c": 6, "s": 4, "q": 4, "t": 2, "a": 7, "z": 0}


def tag(el):
    return el.tag.split("}")[-1]


def fnum(v, default=0.0):
    if v is None:
        return default
    m = NUM.search(str(v))
    return float(m.group()) if m else default


def length(v, ref=0.0, font=16.0, default=0.0):
    m = LENGTH.match(str(v)) if v is not None else None
    if not m:
        return default
    x, unit = float(m.group(1)), m.group(2).lower()
    if unit == "%":
        return x * ref / 100
    if unit in ("em", "ex"):
        return x * font * (0.5 if unit == "ex" else 1)
    return x * UNIT_PX[unit] if unit in UNIT_PX else default


def style_get(el, key):
    st = el.get("style", "")
    m = re.search(rf"(?:^|;)\s*{key}\s*:\s*([^;]+)", st)
    return m.group(1).strip() if m else el.get(key)


def finite(*xs):
    return all(math.isfinite(x) for x in xs)


def add_style(el, decl):
    st = el.get("style", "").strip().rstrip(";")
    el.set("style", f"{st};{decl}" if st else decl)


def fmt(x):
    return f"{x:.2f}".rstrip("0").rstrip(".")


def pt(p):
    return f"{fmt(p[0])},{fmt(p[1])}"


def lerp(a, b, t):
    return (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t)


def split_cubic(p0, c1, c2, p1):
    a, b, c = lerp(p0, c1, 0.5), lerp(c1, c2, 0.5), lerp(c2, p1, 0.5)
    d, e = lerp(a, b, 0.5), lerp(b, c, 0.5)
    m = lerp(d, e, 0.5)
    return (p0, a, d, m), (m, e, c, p1)


def parse_path(d):
    toks = TOK.findall(d)
    segs = []
    cmd = None
    cur = start = (0.0, 0.0)
    last_c = last_q = None
    i = 0
    while i < len(toks):
        if toks[i].isalpha():
            cmd = toks[i]
            i += 1
            if cmd in "Zz":
                segs.append(("Z",))
                cur = start
                last_c = last_q = None
            continue
        if cmd is None or cmd in "Zz":
            i += 1
            continue
        c = cmd.lower()
        n = ARGS[c]
        a = []
        j = i
        while len(a) < n and j < len(toks) and not toks[j].isalpha():
            t = toks[j]
            if c == "a" and len(a) in (3, 4) and len(t) > 1 and t[0] in "01":
                a.append(float(t[0]))
                toks[j] = t[1:]
                continue
            a.append(float(t))
            j += 1
        if len(a) < n:
            break
        i = j
        ox, oy = cur if cmd.islower() else (0.0, 0.0)
        new_c = new_q = None
        if c == "m":
            p = (a[0] + ox, a[1] + oy)
            segs.append(("M", p))
            start = p
            cmd = "l" if cmd.islower() else "L"
        elif c == "l":
            p = (a[0] + ox, a[1] + oy)
            segs.append(("L", p))
        elif c == "h":
            p = (a[0] + ox, cur[1])
            segs.append(("L", p))
        elif c == "v":
            p = (cur[0], a[0] + oy)
            segs.append(("L", p))
        elif c in "cs":
            if c == "c":
                c1 = (a[0] + ox, a[1] + oy)
                rest = a[2:]
            else:
                c1 = (2 * cur[0] - last_c[0], 2 * cur[1] - last_c[1]) if last_c else cur
                rest = a
            c2 = (rest[0] + ox, rest[1] + oy)
            p = (rest[2] + ox, rest[3] + oy)
            segs.append(("C", c1, c2, p))
            new_c = c2
        elif c in "qt":
            if c == "q":
                q1 = (a[0] + ox, a[1] + oy)
                p = (a[2] + ox, a[3] + oy)
            else:
                q1 = (2 * cur[0] - last_q[0], 2 * cur[1] - last_q[1]) if last_q else cur
                p = (a[0] + ox, a[1] + oy)
            c1 = lerp(cur, q1, 2 / 3)
            c2 = lerp(p, q1, 2 / 3)
            segs.append(("C", c1, c2, p))
            new_q = q1
        else:
            p = (a[5] + ox, a[6] + oy)
            segs.append(("A", a[0], a[1], a[2], int(a[3]), int(a[4]), p))
        cur = p
        last_c, last_q = new_c, new_q
    return segs


def transform_scale(spec):
    a, b, c, d = 1.0, 0.0, 0.0, 1.0
    for name, args in TRANSFORM.findall(spec or ""):
        v = [float(n) for n in NUM.findall(args)] or [0.0]
        if name == "matrix" and len(v) == 6:
            m = v[:4]
        elif name == "scale":
            m = (v[0], 0.0, 0.0, v[1] if len(v) > 1 else v[0])
        elif name == "rotate":
            r = math.radians(v[0])
            m = (math.cos(r), math.sin(r), -math.sin(r), math.cos(r))
        elif name in ("skewX", "skewY"):
            k = math.tan(math.radians(v[0]))
            m = (1.0, 0.0, k, 1.0) if name == "skewX" else (1.0, k, 0.0, 1.0)
        else:
            continue
        a, b, c, d = a * m[0] + c * m[1], b * m[0] + d * m[1], a * m[2] + c * m[3], b * m[2] + d * m[3]
    total, det = a * a + b * b + c * c + d * d, a * d - b * c
    if not finite(total, det) or det == 0:
        return 0.0
    return math.sqrt((total + math.sqrt(max(0.0, total * total - 4 * det * det))) / 2)


def shape_animated(el):
    return any(tag(c) in ("animate", "set") and c.get("attributeName") in SHAPE_ATTRS for c in el)


class Rougher:
    def __init__(self, amp, rng, root, box):
        self.base_amp = amp
        self.base_diag = math.hypot(box[2], box[3])
        self.scale = self.outer_scale = 1.0
        self.rng = rng
        self.root = root
        self.box = box
        self.free = True
        self.filter_id = None
        self.font = 16.0

    def size(self, el, key, axis):
        _, _, w, h = self.box
        ref = (w, h, math.hypot(w, h) / math.sqrt(2))[axis]
        return length(el.get(key), ref, self.font)

    @property
    def amp(self):
        return self.base_amp / self.scale

    @property
    def diag(self):
        return self.base_diag / self.scale

    def fits(self, *xs):
        limit = 1e6 * self.diag
        return all(math.isfinite(x) and abs(x) <= limit for x in xs)

    def j(self, scale=1.0):
        return self.rng.gauss(0, self.amp * scale)

    def jx(self, x, scale):
        x0, _, w, _ = self.box
        d = self.j(scale)
        if self.free and x <= x0 + 1:
            return x - abs(d) - 1
        if self.free and x >= x0 + w - 1:
            return x + abs(d) + 1
        return x + d

    def jy(self, y, scale):
        _, y0, _, h = self.box
        d = self.j(scale)
        if self.free and y <= y0 + 1:
            return y - abs(d) - 1
        if self.free and y >= y0 + h - 1:
            return y + abs(d) + 1
        return y + d

    def jp(self, p, scale=0.6):
        return (self.jx(p[0], scale), self.jy(p[1], scale))

    def steps(self, a, b):
        shortest = max(self.amp * 18, self.diag / 160, 1e-9)
        return max(1, min(200, int(math.hypot(b[0] - a[0], b[1] - a[1]) / shortest)))

    def wobble(self, pts, closed):
        out = []
        seq = pts + ([pts[0]] if closed else [])
        for a, b in zip(seq, seq[1:]):
            n = self.steps(a, b)
            for k in range(n):
                out.append(self.jp(lerp(a, b, k / n)))
        if not closed:
            out.append(self.jp(seq[-1]))
        d = "M" + " L".join(pt(p) for p in out)
        return d + ("Z" if closed else "")

    def to_path(self, el, d):
        if shape_animated(el):
            return
        for k in SHAPE_ATTRS:
            el.attrib.pop(k, None)
        el.tag = f"{{{SVG_NS}}}path"
        el.set("d", d)

    def shift(self, el):
        amp = self.base_amp / self.outer_scale
        dx, dy = self.rng.gauss(0, amp * 0.8), self.rng.gauss(0, amp * 0.8)
        tr = el.get("transform", "")
        el.set("transform", f"translate({fmt(dx)},{fmt(dy)}) {tr}".strip())

    def line(self, el):
        p = [(self.size(el, "x1", 0), self.size(el, "y1", 1)), (self.size(el, "x2", 0), self.size(el, "y2", 1))]
        if not self.fits(*p[0], *p[1]):
            return
        self.to_path(el, self.wobble(p, False))
        if tag(el) == "path":
            add_style(el, "fill:none")
        self.shift(el)

    def rect(self, el):
        if fnum(el.get("rx")) or fnum(el.get("ry")):
            return
        x, y, w, h = (self.size(el, k, axis) for k, axis in (("x", 0), ("y", 1), ("width", 0), ("height", 1)))
        if not self.fits(x, y, w, h) or w <= 0 or h <= 0:
            return
        if w >= 0.9 * self.box[2] and h >= 0.9 * self.box[3]:
            return
        self.to_path(el, self.wobble([(x, y), (x + w, y), (x + w, y + h), (x, y + h)], True))

    def poly(self, el, closed):
        nums = [float(n) for n in NUM.findall(el.get("points", ""))]
        pts = list(zip(nums[0::2], nums[1::2]))
        if len(pts) >= 2 and self.fits(*nums):
            self.to_path(el, self.wobble(pts, closed))

    def ellipse(self, el):
        cx, cy = self.size(el, "cx", 0), self.size(el, "cy", 1)
        if tag(el) == "circle":
            rx = ry = self.size(el, "r", 2)
        else:
            rx, ry = self.size(el, "rx", 0), self.size(el, "ry", 1)
        if not self.fits(cx, cy, rx, ry) or rx <= 0 or ry <= 0:
            return
        n = max(12, int(2 * math.pi * max(rx, ry) / (self.amp * 10 + 1e-6)))
        n = min(n, 160)
        drift = self.rng.uniform(0, math.tau)
        pts = []
        for k in range(n):
            a = math.tau * k / n
            r = 1 + 0.5 * self.j(1 / max(rx, ry)) + 0.015 * math.sin(3 * a + drift)
            pts.append((cx + rx * r * math.cos(a), cy + ry * r * math.sin(a)))
        d = "M" + " L".join(pt(p) for p in pts) + "Z"
        small = max(rx, ry) < self.amp * 6
        self.to_path(el, d)
        if small:
            self.shift(el)

    def straight(self, out, a, b):
        n = self.steps(a, b)
        for k in range(1, n):
            out.append("L" + pt(self.jp(lerp(a, b, k / n))))

    def curve(self, out, seg, ja, jb, depth=0):
        p0, c1, c2, p1 = seg
        if depth < 3 and math.hypot(p1[0] - p0[0], p1[1] - p0[1]) > self.amp * 30:
            left, right = split_cubic(*seg)
            jm = self.jp(left[3])
            self.curve(out, left, ja, jm, depth + 1)
            self.curve(out, right, jm, jb, depth + 1)
            return
        da = (ja[0] - p0[0], ja[1] - p0[1])
        db = (jb[0] - p1[0], jb[1] - p1[1])
        k1 = (c1[0] + da[0] + self.j(0.35), c1[1] + da[1] + self.j(0.35))
        k2 = (c2[0] + db[0] + self.j(0.35), c2[1] + db[1] + self.j(0.35))
        out.append(f"C{pt(k1)} {pt(k2)} {pt(jb)}")

    def path(self, el):
        segs = parse_path(el.get("d", ""))
        if not segs or segs[0][0] != "M":
            return
        coords = [v for s in segs for p in s[1:] for v in (p if isinstance(p, tuple) else (p,))]
        if not self.fits(*coords):
            return
        out = []
        cur = start = (0.0, 0.0)
        jcur = jstart = cur
        for s in segs:
            kind = s[0]
            if kind == "M":
                cur = start = s[1]
                jcur = jstart = self.jp(cur)
                out.append("M" + pt(jcur))
            elif kind == "L":
                self.straight(out, cur, s[1])
                cur, jcur = s[1], self.jp(s[1])
                out.append("L" + pt(jcur))
            elif kind == "C":
                jend = self.jp(s[3])
                self.curve(out, (cur, s[1], s[2], s[3]), jcur, jend)
                cur, jcur = s[3], jend
            elif kind == "A":
                _, rx, ry, rot, large, sweep, p = s
                jcur = self.jp(p)
                out.append(f"A{fmt(rx)} {fmt(ry)} {fmt(rot)} {large} {sweep} {pt(jcur)}")
                cur = p
            else:
                self.straight(out, cur, start)
                out.append("Z")
                cur, jcur = start, jstart
        el.set("d", " ".join(out))

    def ensure_filter(self):
        if self.filter_id:
            return self.filter_id
        used = {e.get("id") for e in self.root.iter() if e.get("id")}
        fid, k = "rough-edge", 2
        while fid in used:
            fid, k = f"rough-edge-{k}", k + 1
        defs = next((c for c in self.root if tag(c) == "defs"), None)
        if defs is None:
            defs = ET.Element(f"{{{SVG_NS}}}defs")
            self.root.insert(0, defs)
        f = ET.SubElement(defs, f"{{{SVG_NS}}}filter", {"id": fid, "x": "-5%", "y": "-5%", "width": "110%", "height": "110%"})
        ET.SubElement(f, f"{{{SVG_NS}}}feTurbulence", {"type": "fractalNoise", "baseFrequency": "0.9", "numOctaves": "2", "seed": str(self.rng.randint(1, 9999)), "result": "n"})
        ET.SubElement(f, f"{{{SVG_NS}}}feDisplacementMap", {"in": "SourceGraphic", "in2": "n", "scale": fmt(self.amp * 2.2), "xChannelSelector": "R", "yChannelSelector": "G"})
        self.filter_id = fid
        return fid

    def shake(self, el, chars, k, size):
        n = len(chars)
        if not n:
            return
        if not el.get("rotate"):
            el.set("rotate", " ".join(fmt(self.rng.gauss(0, 2.2 * k)) for _ in range(n)))
        own = el.get("dy", "")
        if len(NUM.findall(own)) > 1:
            return
        offs = [self.rng.gauss(0, self.amp * 0.5 * k) for _ in range(n)]
        dys = [offs[0] + length(own, self.box[3], size)] + [offs[i] - offs[i - 1] for i in range(1, n)]
        el.set("dy", " ".join(fmt(d) for d in dys))

    def text(self, el, size):
        k = 0.25 if size < 16 else (0.6 if size < 32 else 1.0)
        kids = list(el)
        if not kids:
            self.shake(el, el.text or "", k, size)
        else:
            for t in el.iter():
                if t is not el and tag(t) == "tspan" and not list(t):
                    ts = style_get(t, "font-size")
                    tsize = length(ts, size, size, size)
                    self.shake(t, t.text or "", 0.25 if tsize < 16 else (0.6 if tsize < 32 else 1.0), tsize)
        if size >= 28 and not el.get("filter") and "".join(el.itertext()).strip():
            el.set("filter", f"url(#{self.ensure_filter()})")

    def walk(self, el, size):
        fs = style_get(el, "font-size")
        size = length(fs, size, size, size)
        for child in list(el):
            t = tag(child)
            if t in SKIP or child.get("data-rough") == "off":
                continue
            stretch = transform_scale(child.get("transform"))
            if not stretch:
                continue
            was = self.free, self.box, self.scale
            if child.get("transform"):
                self.free = False
            self.outer_scale = self.scale
            self.scale *= stretch
            self.font = length(style_get(child, "font-size"), size, size, size)
            if t == "svg" and not self.enter_viewport(child):
                self.free, self.box, self.scale = was
                continue
            if t == "text":
                self.text(child, self.font)
            else:
                if t == "line":
                    self.line(child)
                elif t == "rect":
                    self.rect(child)
                elif t == "polyline":
                    self.poly(child, False)
                elif t == "polygon":
                    self.poly(child, True)
                elif t in ("circle", "ellipse"):
                    self.ellipse(child)
                elif t == "path":
                    self.path(child)
                self.walk(child, size)
            self.free, self.box, self.scale = was

    def enter_viewport(self, el):
        w, h = (self.size(el, k, axis) if el.get(k) is not None else self.box[axis + 2] for k, axis in (("width", 0), ("height", 1)))
        vb = [float(n) for n in NUM.findall(el.get("viewBox", ""))]
        if len(vb) == 4 and vb[2] > 0 and vb[3] > 0:
            sx, sy = w / vb[2], h / vb[3]
            fit = el.get("preserveAspectRatio", "")
            stretch = max(sx, sy) if "slice" in fit or fit.strip() == "none" else min(sx, sy)
            self.box = vb
        else:
            stretch = 1.0
            self.box = [0.0, 0.0, w, h]
        if not finite(w, h, *self.box, stretch) or w <= 0 or h <= 0 or stretch <= 0:
            return False
        self.scale *= stretch
        self.free = True
        return True


def depth(root):
    deepest = 0
    stack = [(root, 1)]
    while stack:
        el, d = stack.pop()
        deepest = max(deepest, d)
        stack.extend((c, d + 1) for c in el)
    return deepest


def amount(v):
    try:
        x = float(v)
    except ValueError:
        raise argparse.ArgumentTypeError(f"not a number: {v}")
    if not math.isfinite(x) or x < 0:
        raise argparse.ArgumentTypeError(f"must be a finite number of 0 or more: {v}")
    return x


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("src")
    ap.add_argument("dst")
    ap.add_argument("--amount", type=amount, default=1.0)
    ap.add_argument("--seed", type=int)
    args = ap.parse_args()
    rng = random.Random(args.seed) if args.seed is not None else random.SystemRandom()
    tree = ET.parse(args.src)
    root = tree.getroot()
    if depth(root) > MAX_DEPTH:
        raise SystemExit(f"rough.py: {args.src} nests elements more than {MAX_DEPTH} levels deep; flatten the groups and run it again")
    vb = [float(n) for n in NUM.findall(root.get("viewBox", ""))]
    box = vb if len(vb) == 4 else [0.0, 0.0, fnum(root.get("width"), 600), fnum(root.get("height"), 600)]
    w, h = box[2], box[3]
    if not finite(*box) or w <= 0 or h <= 0:
        raise SystemExit(f"rough.py: the canvas size {fmt(w)} x {fmt(h)} from viewBox or width/height is not usable")
    amp = args.amount * math.hypot(w, h) / 720 * 1.4
    Rougher(amp, rng, root, box).walk(root, 16.0)
    tree.write(args.dst, encoding="unicode", xml_declaration=False)
    print(f"wrote {args.dst} (amp {amp:.2f})")


if __name__ == "__main__":
    main()
