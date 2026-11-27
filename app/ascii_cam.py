#!/usr/bin/env python3
"""ASCII Cam: your webcam, live, as ASCII art or Matrix-style digital rain.

    python3 ascii_cam.py                       open the live window
    python3 ascii_cam.py --still IN.jpg OUT.png [--mode matrix] [--cols 160]
                                               render one image (handy for testing)

Keys: Space snapshot (PNG) · Ctrl+C copy frame as text · M switch mode ·
      E edges · F11 fullscreen · H hide the controls · Esc leave fullscreen

How it works:
  * The whole frame is rendered with numpy as an image (glyph atlas lookup), not as
    text, so it keeps up with a 30 fps camera at 200+ columns.
  * The density ramp is measured from the real font: each glyph is rendered and its
    ink coverage decides where it sits, instead of a hand-ordered string.
  * Edge glyphs: a Sobel pass picks | / - \\ along strong edges, so outlines (jaw,
    glasses, shoulders) read as lines instead of mush.
  * No shimmer: auto-levels are smoothed over time and each cell has hysteresis, so
    sensor noise doesn't flip characters back and forth every frame.
  * Matrix mode: falling katakana rain whose brightness is driven by the picture.
"""
import argparse, os, sys, time
import numpy as np
from PySide6.QtCore import QRect, QSize, Qt, QTimer
from PySide6.QtGui import (QColor, QFont, QFontMetrics, QGuiApplication, QImage, QKeySequence,
                           QPainter, QShortcut)

ASCII_CHARS = " .'`^\",:;Il!i><~+-?][}{1)(|\\/tfjrxnuvczXYUJCLQ0OZmwqpdbkhao*#MW&8%B@$"
BLOCK_CHARS = " ░▒▓█"
EDGE_CHARS = "|/-\\"            # edge line at 0°, 45°, 90°, 135° of gradient angle
RAIN_CHARS = "".join(chr(c) for c in range(0xFF66, 0xFF9E)) + "0123456789Z:.=*+-<>¦"
PALETTES = {                    # (name, rgb 0..1); None = source colour
    "Source colour": None,
    "Green phosphor": (0.25, 1.0, 0.4),
    "White": (1.0, 1.0, 1.0),
    "Amber": (1.0, 0.7, 0.15),
    "Cyan": (0.3, 0.9, 1.0),
}
MODES = ["ASCII", "Blocks", "Matrix"]


def qimage_to_array(img):
    """QImage (RGB888 or Grayscale8) -> numpy copy."""
    ch = 3 if img.format() == QImage.Format_RGB888 else 1
    w, h, bpl = img.width(), img.height(), img.bytesPerLine()
    a = np.frombuffer(img.constBits(), np.uint8, bpl * h).reshape(h, bpl)[:, :w * ch]
    return a.reshape(h, w, ch).copy() if ch == 3 else a.copy()


def render_glyphs(chars, font, cw, ch, mirror=False):
    """Rasterise each character into a cw x ch coverage tile (uint8)."""
    out = np.zeros((len(chars), ch, cw), np.uint8)
    img = QImage(cw, ch, QImage.Format_Grayscale8)
    for i, c in enumerate(chars):
        img.fill(0)
        p = QPainter(img)
        p.setRenderHint(QPainter.TextAntialiasing)
        p.setFont(font)
        p.setPen(QColor(255, 255, 255))
        if mirror:                       # the film's rain uses mirrored katakana
            p.translate(cw, 0)
            p.scale(-1, 1)
        p.drawText(QRect(0, 0, cw, ch), Qt.AlignCenter, c)
        p.end()
        out[i] = qimage_to_array(img)
    return out


class GlyphSet:
    """Glyph tiles plus a 256-entry lookup from brightness to the glyph whose ink matches it."""
    def __init__(self, chars, font, cw, ch, mirror=False, by_coverage=True):
        self.chars = chars
        self.tiles = render_glyphs(chars, font, cw, ch, mirror)
        cov = self.tiles.reshape(len(chars), -1).mean(1)
        if by_coverage:
            n = (cov - cov.min()) / max(1e-6, cov.max() - cov.min())
            self.lut = np.abs(n[None, :] - np.linspace(0, 1, 256)[:, None]).argmin(1)
        else:
            self.lut = (np.arange(256) * (len(chars) - 1) + 127) // 255


class Renderer:
    def __init__(self, font_px=12):
        # First installed font wins: Linux, Windows, macOS
        self.font = QFont()
        self.font.setFamilies(["Noto Sans Mono", "DejaVu Sans Mono", "Consolas", "Menlo", "Courier New"])
        self.font.setStyleHint(QFont.Monospace)
        self.font.setPixelSize(font_px)
        fm = QFontMetrics(self.font)
        self.cw, self.ch = fm.horizontalAdvance("M"), fm.height()
        self.ascii = GlyphSet(ASCII_CHARS, self.font, self.cw, self.ch)
        self.blocks = GlyphSet(BLOCK_CHARS, self.font, self.cw, self.ch, by_coverage=False)
        self.edges = GlyphSet(EDGE_CHARS, self.font, self.cw, self.ch, by_coverage=False)
        rain_font = QFont(self.font)
        rain_font.setFamilies(self.font.families() + ["Noto Sans CJK JP", "MS Gothic", "Yu Gothic",
                                                     "Hiragino Sans"])
        self.rain = GlyphSet(RAIN_CHARS, rain_font, self.cw, self.ch, mirror=True, by_coverage=False)
        # Settings
        self.mode, self.palette, self.cols = "ASCII", "Source colour", 140
        self.edges_on, self.invert, self.auto_levels = True, False, True
        self.edge_threshold = 0.5
        self._reset()

    def _reset(self):
        self.grid = None
        self.stable = None             # hysteresis state, per cell
        self.levels = None             # smoothed (lo, hi)
        self.last_t = None
        self.idx, self.chars = np.zeros((0, 0), int), ""

    def grid_for(self, w, h):
        rows = max(1, round(h / w * self.cols * self.cw / self.ch))
        return self.cols, rows

    # --- picture analysis -------------------------------------------------------------
    def analyse(self, rgb2):
        """rgb2: (2*rows, 2*cols, 3) uint8. Returns per-cell colour, brightness 0..1, edges."""
        rows, cols = rgb2.shape[0] // 2, rgb2.shape[1] // 2
        f = rgb2.astype(np.float32)
        lum2 = (f @ np.array([0.2126, 0.7152, 0.0722], np.float32)) / 255.0
        colour = f.reshape(rows, 2, cols, 2, 3).mean((1, 3))
        lum = lum2.reshape(rows, 2, cols, 2).mean((1, 3))

        if self.auto_levels:            # stretch contrast, but smoothly, so it doesn't pump
            lo, hi = np.percentile(lum, (2, 98))
            if self.levels is None:
                self.levels = np.array([lo, hi])
            else:
                self.levels += 0.1 * (np.array([lo, hi]) - self.levels)
            lo, hi = self.levels
            lum = np.clip((lum - lo) / max(0.05, hi - lo), 0, 1)
            lum2 = np.clip((lum2 - lo) / max(0.05, hi - lo), 0, 1)

        # Hysteresis: a cell only changes once it has moved more than a small step,
        # which removes the frame-to-frame shimmer from sensor noise.
        if self.stable is None or self.stable.shape != lum.shape:
            self.stable = lum.copy()
        else:
            moved = np.abs(lum - self.stable) > 0.035
            self.stable[moved] = lum[moved]
        lum = self.stable ** 0.85        # slight lift: glyph ink is sparse, so mids read dark

        edge_idx = None
        if self.edges_on and self.mode == "ASCII":
            p = np.pad(lum2, 1, mode="edge")
            gx = (p[:-2, 2:] + 2 * p[1:-1, 2:] + p[2:, 2:]) - (p[:-2, :-2] + 2 * p[1:-1, :-2] + p[2:, :-2])
            gy = (p[2:, :-2] + 2 * p[2:, 1:-1] + p[2:, 2:]) - (p[:-2, :-2] + 2 * p[:-2, 1:-1] + p[:-2, 2:])
            # Pool orientations per cell with the doubled-angle trick, so opposite gradients
            # on the two sides of a thin line add up instead of cancelling.
            a = (gx * gx - gy * gy).reshape(rows, 2, cols, 2).mean((1, 3))
            b = (2 * gx * gy).reshape(rows, 2, cols, 2).mean((1, 3))
            strength = np.sqrt(np.sqrt(a * a + b * b))
            angle = (np.degrees(0.5 * np.arctan2(b, a)) + 180) % 180    # gradient direction
            bins = ((angle + 22.5) // 45).astype(np.int64) % 4
            edge_idx = np.where(strength > self.edge_threshold, bins, -1)
        return colour, lum, edge_idx

    # --- rendering --------------------------------------------------------------------
    def render(self, frame):
        """frame: QImage from the camera (any format). Returns the rendered QImage."""
        cols, rows = self.grid_for(frame.width(), frame.height())
        if self.grid != (cols, rows):
            self._reset()
            self.grid = (cols, rows)
        small = frame.scaled(cols * 2, rows * 2, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
        rgb2 = qimage_to_array(small.convertToFormat(QImage.Format_RGB888))
        colour, lum, edge_idx = self.analyse(rgb2)
        if self.invert:
            lum = 1 - lum

        if self.mode == "Matrix":
            idx, value, white = self.rain_step(lum)
            tiles = self.rain.tiles
            chars = self.rain.chars
        else:
            gs = self.blocks if self.mode == "Blocks" else self.ascii
            levels = (lum * 255).astype(np.uint8)
            idx = gs.lut[levels]
            tiles, chars = gs.tiles, gs.chars
            if edge_idx is not None:
                # Edge glyphs live after the ramp glyphs in one combined tile table
                tiles = np.concatenate([gs.tiles, self.edges.tiles])
                chars = gs.chars + self.edges.chars
                idx = np.where(edge_idx >= 0, len(gs.chars) + edge_idx, idx)
            value, white = np.ones_like(lum), None

        pal = PALETTES[self.palette]
        if pal is None:
            # Source colour, normalised to full brightness: the glyph's ink already encodes
            # how bright the cell is, so dark colours would otherwise be counted twice.
            c = colour / np.maximum(colour.max(2, keepdims=True), 1)
            c = 0.25 + 0.75 * c
        else:
            c = np.broadcast_to(np.array(pal, np.float32), colour.shape)
        rgb = c * value[..., None]
        if white is not None:            # leading drop of each rain trail glows white
            rgb = rgb + (np.clip(value, 0.5, 1)[..., None] - rgb) * white[..., None]
        cell_rgb = np.ascontiguousarray(np.clip(rgb * 255, 0, 255).astype(np.uint8))

        # Glyph ink (one channel) via a tile lookup, then let Qt's SIMD painter tint it:
        # nearest-neighbour upscale of the per-cell colours, multiplied by the ink.
        ink = np.ascontiguousarray(tiles[idx].transpose(0, 2, 1, 3).reshape(rows * self.ch, cols * self.cw))
        h, w = ink.shape
        tint = QImage(cell_rgb.data, cols, rows, cols * 3, QImage.Format_RGB888)
        out = tint.scaled(w, h, Qt.IgnoreAspectRatio, Qt.FastTransformation).convertToFormat(QImage.Format_RGB32)
        mask = QImage(ink.data, w, h, w, QImage.Format_Grayscale8)
        p = QPainter(out)
        p.setCompositionMode(QPainter.CompositionMode_Multiply)
        p.drawImage(0, 0, mask)
        p.end()
        self.idx, self.chars = idx, chars
        return out

    def text(self):
        """The last frame as plain text."""
        return "\n".join("".join(self.chars[i] for i in row) for row in self.idx)

    def rain_step(self, lum):
        rows, cols = lum.shape
        now = time.monotonic()
        dt = 0 if self.last_t is None else min(0.1, now - self.last_t)
        self.last_t = now
        rng = np.random.default_rng() if not hasattr(self, "rng") else self.rng
        self.rng = rng
        if dt == 0 or not hasattr(self, "heads") or self.heads.shape[1] != cols:
            layers = 3                                           # drops per column
            self.heads = rng.uniform(-rows, rows, (layers, cols))
            self.speeds = rng.uniform(8, 24, (layers, cols))     # cells per second
            self.trails = rng.uniform(6, 26, (layers, cols))
            self.glyphs = rng.integers(0, len(RAIN_CHARS), (rows, cols))
        self.heads += self.speeds * dt
        done = self.heads - self.trails > rows
        self.heads[done] = rng.uniform(-rows * 0.6, 0, done.sum())
        self.speeds[done] = rng.uniform(8, 24, done.sum())
        self.trails[done] = rng.uniform(6, 26, done.sum())

        y = np.arange(rows)[None, :, None]
        d = self.heads[:, None, :] - y                         # layers, rows, cols
        rain = np.where((d >= 0) & (d < self.trails[:, None, :]), 1 - d / self.trails[:, None, :], 0).max(0)
        head = ((d >= 0) & (d < 1)).any(0)
        # Glyphs flicker: a few change every frame, and the head always gets a fresh one
        flip = (rng.random((rows, cols)) < 0.02) | head
        self.glyphs[flip] = rng.integers(0, len(RAIN_CHARS), flip.sum())
        # The picture decides how bright the rain is; a faint floor keeps the face visible
        # between drops.
        value = lum ** 1.2 * (0.22 + 0.78 * rain)
        return self.glyphs, np.clip(value * 1.4, 0, 1), head & (lum > 0.15)


def run_still(args):
    app = QGuiApplication(sys.argv[:1])
    r = Renderer(args.font)
    r.mode, r.cols = args.mode, args.cols
    r.palette = args.palette or ("Green phosphor" if args.mode == "Matrix" else "Source colour")
    img = QImage(args.input)
    if img.isNull():
        sys.exit(f"Can't open {args.input}")
    if args.mode == "Matrix":            # let the rain fill in before saving
        r.render(img)
        for _ in range(60):
            r.last_t -= 1 / 30
            out = r.render(img)
    t = time.perf_counter()
    out = r.render(img)
    ms = (time.perf_counter() - t) * 1000
    out.save(args.output)
    print(f"{args.output}: {r.grid[0]}x{r.grid[1]} cells, {out.width()}x{out.height()} px, {ms:.1f} ms")


def run_gui(args):
    from PySide6.QtMultimedia import QCamera, QMediaCaptureSession, QMediaDevices, QVideoSink
    from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QHBoxLayout, QLabel, QPushButton,
                                   QSlider, QVBoxLayout, QWidget)

    BG, FG, BOX, DIM, ACCENT = "#1e1e2e", "#cdd6f4", "#313244", "#a6adc8", "#89b4fa"

    class View(QWidget):
        def __init__(self):
            super().__init__()
            self.image = None
            self.setMinimumSize(320, 180)

        def show_frame(self, image):
            self.image = image
            self.update()

        def paintEvent(self, _):
            p = QPainter(self)
            p.fillRect(self.rect(), Qt.black)
            if self.image is None:
                p.setPen(QColor(DIM))
                p.drawText(self.rect(), Qt.AlignCenter, "Waiting for the camera…")
                return
            size = self.image.size().scaled(self.size(), Qt.KeepAspectRatio)
            r = QRect(0, 0, size.width(), size.height())
            r.moveCenter(self.rect().center())
            p.setRenderHint(QPainter.SmoothPixmapTransform)
            p.drawImage(r, self.image)

    class Window(QWidget):
        def __init__(self):
            super().__init__()
            self.setWindowTitle("ASCII Cam")
            self.resize(1280, 820)
            self.setStyleSheet(f"""
                QWidget {{ background:{BG}; color:{FG}; }}
                QPushButton, QComboBox {{ background:{BOX}; border:none; border-radius:6px; padding:6px 12px; }}
                QPushButton:hover {{ background:#45475a; }}
                QSlider::groove:horizontal {{ height:4px; background:{BOX}; border-radius:2px; }}
                QSlider::handle:horizontal {{ background:{ACCENT}; width:14px; margin:-5px 0; border-radius:7px; }}
                QLabel#status {{ color:{DIM}; }}""")
            self.r = Renderer(args.font)
            self.r.cols = args.cols
            self.frame, self.new = None, False
            self.times = []

            self.camera_box = QComboBox()
            self.cams = QMediaDevices.videoInputs()
            for i, d in enumerate(self.cams):
                self.camera_box.addItem(f"{i + 1}: {d.description()}")
            self.camera_box.currentIndexChanged.connect(self.start_camera)
            self.mode_box = QComboBox()
            self.mode_box.addItems(MODES)
            self.mode_box.currentTextChanged.connect(self.set_mode)
            self.pal_box = QComboBox()
            self.pal_box.addItems(PALETTES)
            self.pal_box.currentTextChanged.connect(lambda t: setattr(self.r, "palette", t))
            self.cols_slider = QSlider(Qt.Horizontal, minimum=40, maximum=320, value=args.cols)
            self.cols_slider.setFixedWidth(180)
            self.cols_label = QLabel(f"{args.cols} cols")
            self.cols_slider.valueChanged.connect(self.set_cols)
            self.edges = QCheckBox("Edges", checked=True, toggled=lambda v: setattr(self.r, "edges_on", v))
            self.invert = QCheckBox("Invert", toggled=lambda v: setattr(self.r, "invert", v))
            self.mirror = QCheckBox("Mirror", checked=True)
            snap = QPushButton("Snapshot", clicked=self.snapshot)

            self.bar = QWidget()
            bar = QHBoxLayout(self.bar)
            bar.setContentsMargins(0, 0, 0, 0)
            for w in (self.camera_box, self.mode_box, self.pal_box, QLabel("Detail"), self.cols_slider,
                      self.cols_label, self.edges, self.invert, self.mirror):
                bar.addWidget(w)
            bar.addStretch()
            bar.addWidget(snap)
            self.view = View()
            self.status = QLabel(objectName="status")
            lay = QVBoxLayout(self)
            lay.addWidget(self.bar)
            lay.addWidget(self.view, 1)
            lay.addWidget(self.status)

            QShortcut(QKeySequence(Qt.Key_Space), self, self.snapshot)
            QShortcut(QKeySequence.Copy, self, self.copy_text)
            QShortcut(QKeySequence(Qt.Key_M), self,
                      lambda: self.mode_box.setCurrentIndex((self.mode_box.currentIndex() + 1) % len(MODES)))
            QShortcut(QKeySequence(Qt.Key_E), self, self.edges.toggle)
            QShortcut(QKeySequence(Qt.Key_H), self, lambda: (self.bar.setVisible(not self.bar.isVisible()),
                                                            self.status.setVisible(self.bar.isVisible())))
            QShortcut(QKeySequence(Qt.Key_F11), self,
                      lambda: self.showNormal() if self.isFullScreen() else self.showFullScreen())
            QShortcut(QKeySequence(Qt.Key_Escape), self, self.showNormal)

            self.session = QMediaCaptureSession()
            self.sink = QVideoSink()
            self.sink.videoFrameChanged.connect(self.on_frame)
            self.session.setVideoSink(self.sink)
            self.camera = None
            self.start_camera(min(args.camera, max(0, len(self.cams) - 1)))
            self.camera_box.setCurrentIndex(min(args.camera, max(0, len(self.cams) - 1)))
            # Render on a timer from the newest frame, so slow frames are dropped, not queued
            self.timer = QTimer(self, interval=15, timeout=self.tick)
            self.timer.start()

        def start_camera(self, i):
            if self.camera:
                self.camera.stop()
            if not self.cams:
                self.status.setText("No camera found")
                return
            dev = self.cams[i]
            # Prefer ~720p: plenty for 300 columns and much cheaper to convert than 1080p
            fmts = [f for f in dev.videoFormats() if f.resolution().height() <= 720]
            self.camera = QCamera(dev)
            if fmts:
                best = max(fmts, key=lambda f: (f.resolution().height(), f.maxFrameRate()))
                self.camera.setCameraFormat(best)
            self.camera.errorOccurred.connect(lambda e, s: self.status.setText(f"Camera error: {s}"))
            self.session.setCamera(self.camera)
            self.camera.start()

        def set_mode(self, m):
            self.r.mode = m
            if m == "Matrix" and self.pal_box.currentText() == "Source colour":
                self.pal_box.setCurrentText("Green phosphor")
            self.edges.setEnabled(m == "ASCII")

        def set_cols(self, v):
            self.r.cols = v
            self.cols_label.setText(f"{v} cols")

        def on_frame(self, frame):
            self.frame, self.new = frame, True

        def tick(self):
            if not self.new or self.frame is None:
                return
            self.new = False
            t = time.perf_counter()
            img = self.frame.toImage()
            if img.isNull():
                return
            if self.mirror.isChecked():
                img = img.flipped(Qt.Horizontal)
            out = self.r.render(img)
            self.view.show_frame(out)
            now = time.perf_counter()
            self.times = [x for x in self.times if now - x < 1] + [now]
            c, rws = self.r.grid
            self.status.setText(f"{img.width()}×{img.height()} camera → {c}×{rws} characters · "
                                f"{len(self.times)} fps · {(now - t) * 1000:.0f} ms/frame · "
                                "Space snapshot · Ctrl+C copy text · M mode · H hide · F11 fullscreen")

        def snapshot(self):
            if self.view.image is None:
                return
            folder = os.path.expanduser("~/Pictures/ASCII Cam")
            os.makedirs(folder, exist_ok=True)
            path = os.path.join(folder, time.strftime("ascii-cam-%Y%m%d-%H%M%S.png"))
            self.view.image.save(path)
            self.status.setText(f"Saved {path}")

        def copy_text(self):
            QGuiApplication.clipboard().setText(self.r.text())
            self.status.setText("Copied the frame as text")

    app = QApplication(sys.argv[:1])
    app.setDesktopFileName("ascii-cam")     # matches ascii-cam.desktop, so the taskbar shows its icon
    icon = os.path.join(os.path.dirname(os.path.abspath(__file__)), "icon.svg")
    if os.path.exists(icon):
        from PySide6.QtGui import QIcon
        app.setWindowIcon(QIcon(icon))
    app.setDesktopFileName("ascii-cam")
    win = Window()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="Live webcam as ASCII art or Matrix rain.")
    p.add_argument("--still", nargs=2, metavar=("IN", "OUT"), help="render one image and exit")
    p.add_argument("--mode", choices=MODES, default="ASCII")
    p.add_argument("--palette", choices=list(PALETTES))
    p.add_argument("--cols", type=int, default=140)
    p.add_argument("--font", type=int, default=12, help="glyph pixel size (default 12)")
    p.add_argument("--camera", type=int, default=0, help="camera index (default 0)")
    a = p.parse_args()
    if a.still:
        a.input, a.output = a.still
        run_still(a)
    else:
        run_gui(a)
