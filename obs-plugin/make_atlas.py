#!/usr/bin/env python3
"""Builds data/atlas.png: one row of glyph tiles the shader indexes into.

Layout (must match GLYPH_* constants in data/ascii-cam.effect):
  0-15   ASCII ramp, 16 glyphs picked evenly by measured ink coverage (dark -> bright)
  16-19  edge glyphs | / - \\
  20-24  blocks      space ░ ▒ ▓ █
  25-88  Matrix rain: 64 mirrored half-width katakana and digits
"""
import os, sys
import numpy as np
from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QColor, QFont, QGuiApplication, QImage, QPainter

TW, TH = 24, 48                    # tile size in the atlas; the shader scales it to any cell size
ASCII = " .'`^\",:;Il!i><~+-?][}{1)(|\\/tfjrxnuvczXYUJCLQ0OZmwqpdbkhao*#MW&8%B@$"
EDGES = "|/-\\"
BLOCKS = " ░▒▓█"
RAIN = ("".join(chr(c) for c in range(0xFF66, 0xFF9E)) + "0123456789Z:.=*+-<>¦")[:64]


def tile(ch, font, mirror=False):
    img = QImage(TW, TH, QImage.Format_Grayscale8)
    img.fill(0)
    p = QPainter(img)
    p.setRenderHint(QPainter.TextAntialiasing)
    p.setFont(font)
    p.setPen(QColor(255, 255, 255))
    if mirror:
        p.translate(TW, 0)
        p.scale(-1, 1)
    p.drawText(QRect(0, 0, TW, TH), Qt.AlignCenter, ch)
    p.end()
    a = np.frombuffer(img.constBits(), np.uint8, img.bytesPerLine() * TH).reshape(TH, -1)[:, :TW]
    return a.copy()


def main():
    QGuiApplication(sys.argv[:1])
    font = QFont("Noto Sans Mono")
    font.setStyleHint(QFont.Monospace)
    font.setPixelSize(int(TH * 0.72))
    rain_font = QFont(font)
    rain_font.setFamilies(["Noto Sans Mono", "Noto Sans CJK JP"])

    ascii_tiles = [tile(c, font) for c in ASCII]
    cov = np.array([t.mean() for t in ascii_tiles])
    cov = (cov - cov.min()) / (cov.max() - cov.min())
    picked = []
    for target in np.linspace(0, 1, 16):          # the glyph closest to each ink level
        order = np.argsort(np.abs(cov - target))
        picked.append(next(i for i in order if i not in picked))
    picked.sort(key=lambda i: cov[i])
    print("ASCII ramp:", "".join(ASCII[i] for i in picked))

    tiles = [ascii_tiles[i] for i in picked]
    tiles += [tile(c, font) for c in EDGES]
    tiles += [tile(c, font) for c in BLOCKS]
    tiles += [tile(c, rain_font, mirror=True) for c in RAIN]
    atlas = np.ascontiguousarray(np.hstack(tiles))
    h, w = atlas.shape
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "atlas.png")
    QImage(atlas.data, w, h, w, QImage.Format_Grayscale8).convertToFormat(QImage.Format_RGBA8888).save(out)
    print(f"{out}: {len(tiles)} glyphs, {w}x{h}")


if __name__ == "__main__":
    main()
