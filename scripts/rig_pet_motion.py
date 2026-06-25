#!/usr/bin/env python3
"""Puppet-rig a front-facing petdex sprite into 7 characterful, seam-free states.

WHY: petdex sprites whose state rows are near-identical to idle make the pet look
static. Whole-sprite squash/rotate (see animate_pet_motion.py) reads as a rigid
tilting block — it can't articulate. This rigs the sprite instead: the BODY stays
whole (no cuts = no seams), the FLAME is color-isolated and animated freely
(flicker/sway/grow/shrink/burst — canon: a Charmander's tail flame reflects its
mood/health), the FEET are lifted from a clean bottom region, and whole-sprite
squash/stretch is reserved for the jump beat only.

Per-state motion (Codex 9-row taxonomy rows in parens):
  idle(0)    breathe + lazy flame flicker
  waving(3)  lean side-to-side + big flame sway + hop
  jumping(4) crouch -> launch tall -> peak -> land squash, flame roars
  failed(5)  body slumps/flattens + flame shrinks to a sad ember
  waiting(6) small low flame, bored foot tap, low energy
  running(7) feet pump + bob + flame leans back (motion trail)
  review(8)  flame shrinks small + slow pulse (pondering)

Always rebuilds FROM spritesheet.original.<ext> so reruns never compound.
  python scripts/rig_pet_motion.py [slug]            # bake (default charmander)
  python scripts/rig_pet_motion.py <slug> --restore  # undo
Tuned for the front-facing Charmander flame geometry; --restore + fall back to
animate_pet_motion.py for non-Charmander sheets.
"""
from __future__ import annotations
import argparse
import math
import shutil
import sys
from pathlib import Path
from PIL import Image
import numpy as np

try:
    _BICUBIC = Image.Resampling.BICUBIC
    _LANCZOS = Image.Resampling.LANCZOS
except AttributeError:  # pragma: no cover - older Pillow
    _BICUBIC = Image.BICUBIC  # type: ignore[attr-defined]
    _LANCZOS = Image.LANCZOS  # type: ignore[attr-defined]

from agent.pet import constants, store

FW, FH = constants.FRAME_W, constants.FRAME_H

# Codex 9-row taxonomy -> rows we draw. waiting(6) reuses idle-derived low-energy.
ROW = {"idle": 0, "waving": 3, "jumping": 4, "failed": 5, "waiting": 6,
       "running": 7, "review": 8}
NFRAMES = {"idle": 6, "waving": 6, "jumping": 6, "failed": 6, "waiting": 6,
           "running": 8, "review": 6}

# Flame geometry in TIGHT (bbox-cropped) coords — Charmander front pose.
FLAME_BOX = (98, 28, None, 82)     # x1 filled from TW at runtime
FLAME_PIVOT = (104, 80)
LFOOT_BOX = (16, 120, 44, None)    # y1 filled from TH
RFOOT_BOX = (66, 120, 92, None)


def _backup_path(src: Path) -> Path:
    return src.parent / f"spritesheet.original{src.suffix}"


def restore(slug: str) -> int:
    pet = store.resolve_active_pet(slug)
    if not pet or not pet.exists:
        print(f"pet '{slug}' not found", file=sys.stderr); return 1
    src = Path(pet.spritesheet); backup = _backup_path(src)
    if not backup.exists():
        print(f"no backup at {backup}", file=sys.stderr); return 1
    shutil.copy2(backup, src); print(f"restored {backup} -> {src}"); return 0


class Rig:
    def __init__(self, tight: Image.Image):
        self.tight = tight
        self.TW, self.TH = tight.size
        fb = (FLAME_BOX[0], FLAME_BOX[1], self.TW, FLAME_BOX[3])
        self.FLAME_BOX = fb
        self.LFOOT = (LFOOT_BOX[0], LFOOT_BOX[1], LFOOT_BOX[2], self.TH)
        self.RFOOT = (RFOOT_BOX[0], RFOOT_BOX[1], RFOOT_BOX[2], self.TH)
        self.flame, self.fmask, self.foff = self._isolate_flame()
        self.body_nofeet = self._erase_feet(self._body_no_flame())

    def _isolate_flame(self):
        x0, y0, x1, y1 = self.FLAME_BOX
        region = np.asarray(self.tight.crop((x0, y0, x1, y1))).astype(int)
        R, G, B, A = region[..., 0], region[..., 1], region[..., 2], region[..., 3]
        mask = (A > 40) & (R > 190) & (G > 110) & (B < 150) & ((R + G) > 360)
        out = np.zeros_like(region); out[mask] = region[mask]
        f = Image.new("RGBA", (self.TW, self.TH), (0, 0, 0, 0))
        f.paste(Image.fromarray(out.astype("uint8"), "RGBA"), (x0, y0))
        return f, mask, (x0, y0)

    def _body_no_flame(self):
        arr = np.asarray(self.tight).copy()
        bx0, by0 = self.foff; h, w = self.fmask.shape
        arr[by0:by0+h, bx0:bx0+w][self.fmask] = (0, 0, 0, 0)
        return Image.fromarray(arr, "RGBA")

    def _erase_feet(self, b):
        arr = np.asarray(b).copy()
        for x0, y0, x1, y1 in (self.LFOOT, self.RFOOT):
            arr[y0:y1, x0:x1] = (0, 0, 0, 0)
        return Image.fromarray(arr, "RGBA")

    @staticmethod
    def _rot(layer, pivot, deg):
        return layer if deg == 0 else layer.rotate(deg, resample=_BICUBIC, center=pivot)

    def _scale_flame(self, deg, grow):
        f = self._rot(self.flame, FLAME_PIVOT, deg)
        if abs(grow - 1.0) < 0.01:
            return f
        px, py = FLAME_PIVOT
        big = f.resize((max(1, round(self.TW*grow)), max(1, round(self.TH*grow))), _LANCZOS)
        out = Image.new("RGBA", (self.TW, self.TH), (0, 0, 0, 0))
        out.alpha_composite(big, (round(px - px*grow), round(py - py*grow)))
        return out

    def _lift_foot(self, box, dy):
        out = Image.new("RGBA", (self.TW, self.TH), (0, 0, 0, 0))
        x0, y0, x1, y1 = box
        out.paste(self.tight.crop((x0, y0, x1, y1)), (x0, y0 - dy))
        return out

    def _squash(self, layer, sx, sy):
        if abs(sx-1) < 0.01 and abs(sy-1) < 0.01:
            return layer
        nw, nh = max(1, round(self.TW*sx)), max(1, round(self.TH*sy))
        r = layer.resize((nw, nh), _LANCZOS)
        out = Image.new("RGBA", (self.TW, self.TH), (0, 0, 0, 0))
        out.alpha_composite(r, (round((self.TW-nw)/2), self.TH-nh))  # bottom-anchored
        return out

    def frame(self, bb, *, bob=0.0, flame_deg=0.0, flame_grow=1.0, lean=0.0,
              foot_l=0, foot_r=0, sx=1.0, sy=1.0, lift_all=0):
        b = int(round(bob))
        cur = Image.new("RGBA", (self.TW, self.TH), (0, 0, 0, 0))
        cur.alpha_composite(self.body_nofeet, (0, -b))
        cur.alpha_composite(self._scale_flame(flame_deg, flame_grow), (0, -b))
        cur.alpha_composite(self._lift_foot(self.LFOOT, foot_l), (0, -b))
        cur.alpha_composite(self._lift_foot(self.RFOOT, foot_r), (0, -b))
        if lean:
            cur = self._rot(cur, (self.TW//2, self.TH-4), lean)
        cur = self._squash(cur, sx, sy)
        out = Image.new("RGBA", (FW, FH), (0, 0, 0, 0))
        out.paste(cur, (bb[0], bb[1] - lift_all))
        return out


def _s(t): return math.sin(2*math.pi*t)


def gen_state(rig: Rig, bb, state: str):
    n = NFRAMES[state]; out = []
    for i in range(n):
        t = i/n
        if state == "idle":
            out.append(rig.frame(bb, bob=2.5*abs(_s(t)), flame_deg=7*_s(t), flame_grow=1+0.12*_s(t*2)))
        elif state == "running":
            # punchy: deep bob + body lean forward/back + strong flame trail + feet
            out.append(rig.frame(bb, bob=4.0*abs(_s(t*2)), flame_deg=-18*_s(t), lean=5*_s(t),
                                 foot_l=8 if _s(t) > 0 else 0, foot_r=8 if _s(t) < 0 else 0))
        elif state == "review":
            # small flame + clear slow pulse so "thinking" reads
            out.append(rig.frame(bb, bob=1.5*abs(_s(t)), flame_deg=5*_s(t), flame_grow=0.6+0.18*abs(_s(t))))
        elif state == "waving":
            out.append(rig.frame(bb, bob=4*abs(_s(t)), flame_deg=18*_s(t), lean=7*_s(t)))
        elif state == "jumping":
            seq = [(0, 1.06, 0.9, 1.0), (14, 0.95, 1.12, 1.3), (26, 0.98, 1.05, 1.5),
                   (10, 1.0, 1.02, 1.25), (0, 1.08, 0.86, 0.95), (0, 1.02, 0.97, 1.0)]
            lift_all, sx, sy, grow = seq[i % len(seq)]
            out.append(rig.frame(bb, lift_all=lift_all, sx=sx, sy=sy, flame_grow=grow))
        elif state == "waiting":
            # low bored energy: small flame, periodic foot tap + flame dip
            tap = 7 if (i % 3 == 0) else 0
            out.append(rig.frame(bb, bob=0.8*abs(_s(t)), flame_deg=6*_s(t*2), flame_grow=0.55+0.08*_s(t), foot_r=tap))
        elif state == "failed":
            k = i/(n-1)
            out.append(rig.frame(bb, sx=1.0+0.10*k, sy=1.0-0.16*k,
                                 flame_grow=max(0.22, 1-0.75*k), flame_deg=5*k))
    return out


def bake(slug: str) -> int:
    pet = store.resolve_active_pet(slug)
    if not pet or not pet.exists:
        print(f"pet '{slug}' not found", file=sys.stderr); return 1
    src = Path(pet.spritesheet); backup = _backup_path(src)
    if not backup.exists():
        shutil.copy2(src, backup); print(f"backed up original -> {backup}")
    else:
        print(f"backup exists -> {backup} (rebuilding from it)")

    sheet = Image.open(str(backup)).convert("RGBA")
    rows = sheet.height // FH
    if rows < len(constants.CODEX_STATE_ROWS):
        print(f"sheet has {rows} rows; needs the 9-row Codex layout", file=sys.stderr); return 1
    cols = sheet.width // FW
    cell = sheet.crop((0, 0, FW, FH))
    bb = cell.getbbox()
    tight = cell.crop(bb)
    rig = Rig(tight)
    print(f"anchor bbox={bb} tight={tight.size} cols={cols} rows={rows}")

    for state, row in ROW.items():
        frames = gen_state(rig, bb, state)
        for f, img in enumerate(frames):
            cx, cy = f * FW, row * FH
            sheet.paste((0, 0, 0, 0), (cx, cy, cx + FW, cy + FH))  # clear
            sheet.alpha_composite(img, (cx, cy))
        for f in range(len(frames), cols):  # blank trailing
            cx, cy = f * FW, row * FH
            sheet.paste((0, 0, 0, 0), (cx, cy, cx + FW, cy + FH))
        print(f"  row {row} ({state}): {len(frames)} frames")

    fmt = "WEBP" if src.suffix.lower() == ".webp" else "PNG"
    sheet.save(str(src), fmt, lossless=True)
    print(f"wrote {src}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Puppet-rig a front-facing petdex sprite.")
    ap.add_argument("slug", nargs="?", default="charmander")
    ap.add_argument("--restore", action="store_true")
    a = ap.parse_args()
    return restore(a.slug) if a.restore else bake(a.slug)


if __name__ == "__main__":
    raise SystemExit(main())
