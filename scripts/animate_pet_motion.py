#!/usr/bin/env python3
"""Synthesize distinct motion frames for a flat petdex sprite.

Some AI-generated petdex sheets render fine but have state rows that are nearly
identical to idle (run/wave/review/failed barely differ), so the desktop/TUI pet
looks static while the agent works. We can't invent a new limb pose that isn't
in the source art, but we CAN apply squash/stretch + rotation + translation to
the clean idle silhouette to produce genuine, readable motion per state — the
classic "give a static sprite life" trick.

Regenerates only the rows the Hermes state machine actually draws (Codex 9-row
taxonomy): 0 idle, 3 waving, 4 jumping, 5 failed, 7 running, 8 review. PRESERVES
row 6 (waiting) — its curled small pose is already distinct. Rows 1,2
(running-right/left) are never selected by the state machine and are left as-is.

Always regenerates FROM a pristine backup (``spritesheet.original.<ext>``) so
re-runs never compound. Diagnose first with ``scripts/inspect_pet_frames.py``;
this is the fix when a sheet's grid is sound but its poses are too similar.

Usage:
    python scripts/animate_pet_motion.py [slug]            # default: charmander
    python scripts/animate_pet_motion.py charmander --restore   # undo (restore original)

Verify after with inspect_pet_frames.py (intra-row motion should jump from
~3-9% to ~10-16%) and reload the desktop app.
"""
from __future__ import annotations

import argparse
import math
import shutil
import sys
from pathlib import Path

from PIL import Image

try:  # Pillow >= 9.1 moved filters onto Image.Resampling
    _LANCZOS = Image.Resampling.LANCZOS
    _BICUBIC = Image.Resampling.BICUBIC
except AttributeError:  # pragma: no cover - older Pillow
    _LANCZOS = Image.LANCZOS  # type: ignore[attr-defined]
    _BICUBIC = Image.BICUBIC  # type: ignore[attr-defined]

from agent.pet import constants, store

FW, FH = constants.FRAME_W, constants.FRAME_H  # 192 x 208

# Codex 9-row taxonomy → the rows the state machine draws.
ROW = {"idle": 0, "waving": 3, "jumping": 4, "failed": 5, "running": 7, "review": 8}


def _measure_anchor(base_sheet: Image.Image) -> tuple[int, int, tuple[int, int, int, int]]:
    """Return (center_x, foot_y, tight_bbox) of the idle frame-0 character."""
    cell = base_sheet.crop((0, 0, FW, FH))
    bb = cell.getbbox() or (FW // 2 - 1, FH // 2 - 1, FW // 2 + 1, FH // 2 + 1)
    center_x = (bb[0] + bb[2]) // 2
    foot_y = bb[3]
    return center_x, foot_y, bb


def _transform(base: Image.Image, *, angle: float, sx: float, sy: float) -> Image.Image:
    w, h = base.size
    img = base
    if sx != 1.0 or sy != 1.0:
        img = img.resize((max(1, round(w * sx)), max(1, round(h * sy))), _LANCZOS)
    if angle:
        img = img.rotate(angle, resample=_BICUBIC, expand=True)
    return img


def _place(sheet, row, frame, t_img, *, center_x, foot_y, dx, dy):
    cx0, cy0 = frame * FW, row * FH
    sheet.paste((0, 0, 0, 0), (cx0, cy0, cx0 + FW, cy0 + FH))  # clear cell
    px = cx0 + center_x - t_img.width // 2 + dx
    py = cy0 + (foot_y + dy) - t_img.height
    sheet.alpha_composite(t_img, (px, py))


# Each generator returns a list of (transformed_image, dx, dy). dy<0 lifts.
def _gen_idle(b):
    return [(_transform(b, angle=0, sx=1, sy=1), 0, -round(2 * abs(math.sin(math.pi * i / 6)))) for i in range(6)]


def _gen_run(b):
    out = []
    for i in range(6):
        t = i / 6
        bounce = -round(11 * abs(math.sin(math.pi * t * 2)))
        out.append((_transform(b, angle=12 + 4 * math.sin(2 * math.pi * t), sx=1, sy=1),
                    round(5 * math.sin(2 * math.pi * t)), bounce))
    return out


def _gen_jump(b):
    specs = [(0, 1.06, 0.88, 0), (0, 0.94, 1.12, -20), (0, 0.98, 1.05, -38),
             (0, 1.0, 1.02, -18), (0, 1.08, 0.86, 0)]
    return [(_transform(b, angle=a, sx=sx, sy=sy), 0, dy) for a, sx, sy, dy in specs]


def _gen_failed(b):
    angles = [6, 13, 20, 27, 32, 32]
    dys = [2, 5, 8, 11, 14, 14]
    return [(_transform(b, angle=a, sx=1, sy=1), 0, d) for a, d in zip(angles, dys)]


def _gen_review(b):
    out = []
    for i in range(6):
        t = i / 6
        out.append((_transform(b, angle=-9 + 2 * math.sin(2 * math.pi * t), sx=1, sy=1),
                    0, -round(2 * abs(math.sin(math.pi * t)))))
    return out


def _gen_wave(b):
    out = []
    for i in range(6):
        t = i / 6
        out.append((_transform(b, angle=16 * math.sin(2 * math.pi * t), sx=1, sy=1),
                    0, -round(5 * abs(math.sin(2 * math.pi * t)))))
    return out


GEN = {"idle": _gen_idle, "running": _gen_run, "jumping": _gen_jump,
       "failed": _gen_failed, "review": _gen_review, "waving": _gen_wave}


def _backup_path(src: Path) -> Path:
    return src.parent / f"spritesheet.original{src.suffix}"


def restore(slug: str) -> int:
    pet = store.resolve_active_pet(slug)
    if not pet or not pet.exists:
        print(f"pet '{slug}' not found", file=sys.stderr)
        return 1
    src = Path(pet.spritesheet)
    backup = _backup_path(src)
    if not backup.exists():
        print(f"no backup at {backup} — nothing to restore", file=sys.stderr)
        return 1
    shutil.copy2(backup, src)
    print(f"restored original {backup} -> {src}")
    return 0


def generate(slug: str) -> int:
    pet = store.resolve_active_pet(slug)
    if not pet or not pet.exists:
        print(f"pet '{slug}' not found", file=sys.stderr)
        return 1
    src = Path(pet.spritesheet)
    backup = _backup_path(src)
    if not backup.exists():
        shutil.copy2(src, backup)
        print(f"backed up original -> {backup}")
    else:
        print(f"backup exists -> {backup} (regenerating from it)")

    sheet = Image.open(str(backup)).convert("RGBA")
    rows = sheet.height // FH
    if rows < len(constants.CODEX_STATE_ROWS):
        print(f"sheet has {rows} rows; this tool targets the 9-row Codex taxonomy", file=sys.stderr)
        return 1

    center_x, foot_y, bb = _measure_anchor(sheet)
    base = sheet.crop(bb)  # tight idle silhouette
    print(f"anchor: center_x={center_x} foot_y={foot_y} base={base.size}")

    for name, gen in GEN.items():
        row = ROW[name]
        frames = gen(base)
        for f, (t_img, dx, dy) in enumerate(frames):
            _place(sheet, row, f, t_img, center_x=center_x, foot_y=foot_y, dx=dx, dy=dy)
        for f in range(len(frames), sheet.width // FW):  # blank trailing cells
            cx, cy = f * FW, row * FH
            sheet.paste((0, 0, 0, 0), (cx, cy, cx + FW, cy + FH))
        print(f"  row {row} ({name}): {len(frames)} frames")

    fmt = "WEBP" if src.suffix.lower() == ".webp" else "PNG"
    sheet.save(str(src), fmt, lossless=True)
    print(f"wrote {src}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Synthesize motion frames for a flat petdex sprite.")
    ap.add_argument("slug", nargs="?", default="charmander", help="pet slug (default: charmander)")
    ap.add_argument("--restore", action="store_true", help="restore the original sheet from backup")
    args = ap.parse_args()
    return restore(args.slug) if args.restore else generate(args.slug)


if __name__ == "__main__":
    raise SystemExit(main())
