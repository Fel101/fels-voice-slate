"""The timeline views: waveform, spectrogram, the overview strip and the live recording view.

The waveform, spectrogram and overview share one `ViewRange` (the visible stretch of the master timeline), so zooming or
panning any of them moves all three. The views only draw, and report what the pointer did (`selectRequested`,
`seekRequested`, `clickRequested`, `contextRequested`); the window turns those into session calls.
"""
import math

import numpy as np
from PySide6.QtCore import QObject, QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QBrush, QFont, QImage, QPainter, QPen, QPixmap, QPolygonF
from PySide6.QtWidgets import QWidget

from .. import audio_core as ac
from . import theme
from .palette import color

GUTTER = 50                 # room at the left for the amplitude or frequency labels
RIGHT = 12
RULER = 28
EDGE_GRAB = 6               # pixels either side of a selection edge that pick it up
CLICK_SLOP = 3              # pixels the pointer may move and still count as a click
MAX_COLUMNS = 4000


# ---- the visible range ------------------------------------------------------------------------------------------------

class ViewRange(QObject):
    """The visible part of the timeline in master seconds. Shared by the waveform, spectrogram and overview."""

    changed = Signal()
    MIN_SPAN = 0.05

    def __init__(self, parent=None):
        super().__init__(parent)
        self.seconds = 0.0
        self.t0, self.t1 = 0.0, 1.0

    @property
    def span(self):
        return self.t1 - self.t0

    @property
    def zoom(self):
        return self.seconds / self.span if self.span > 0 else 1.0

    def reset(self, seconds):
        """A new take: show all of it."""
        self.seconds = max(0.0, float(seconds))
        self.t0, self.t1 = 0.0, max(self.seconds, self.MIN_SPAN)
        self.changed.emit()

    def fit(self):
        self.set(0.0, self.seconds)

    def set(self, t0, t1):
        total = max(self.seconds, self.MIN_SPAN)
        span = min(max(t1 - t0, self.MIN_SPAN), total)
        t0 = min(max(t0, 0.0), total - span)
        if (t0, t0 + span) != (self.t0, self.t1):
            self.t0, self.t1 = t0, t0 + span
            self.changed.emit()

    def zoom_by(self, factor, anchor):
        """Zoom in by `factor` (below 1 zooms out), keeping the time under `anchor` where it is on screen."""
        span = self.span / factor
        fraction = (anchor - self.t0) / self.span if self.span else 0.5
        self.set(anchor - fraction * span, anchor + (1.0 - fraction) * span)

    def pan_by(self, seconds):
        self.set(self.t0 + seconds, self.t1 + seconds)

    def zoom_to(self, a, b, margin=0.12):
        pad = (b - a) * margin
        self.set(a - pad, b + pad)

    def ensure_visible(self, t, lead=0.08):
        """Scroll (not zoom) so that `t` is on screen, with `lead` of the width before it."""
        if t < self.t0 or t > self.t1:
            self.set(t - self.span * lead, t - self.span * lead + self.span)


# ---- shared drawing ---------------------------------------------------------------------------------------------------

def nice_step(span, target):
    raw = span / target
    magnitude = 10 ** math.floor(math.log10(raw))
    for m in (1, 2, 5, 10):
        if raw <= m * magnitude:
            return m * magnitude
    return 10 * magnitude


def clock_label(t, step):
    decimals = max(0, -math.floor(math.log10(step) + 1e-9)) if step < 1 else 0
    return f'{int(t // 60)}:{t % 60:0{3 + decimals if decimals else 2}.{decimals}f}'


def draw_ruler(p, width, t0, t1, x_of):
    p.fillRect(QRectF(0, 0, width, RULER), color('bg1'))
    p.setPen(QPen(color('line2'), 1))
    p.drawLine(QPointF(0, RULER - 0.5), QPointF(width, RULER - 0.5))
    major = nice_step(t1 - t0, 9)
    minor = major / 5
    p.setFont(theme.ui_font(10))
    for k in range(math.ceil(t0 / minor - 1e-9), math.floor(t1 / minor + 1e-9) + 1):
        t = round(k * minor, 9)
        x = x_of(t)
        if x < GUTTER - 1 or x > width - RIGHT + 1:
            continue
        is_major = k % 5 == 0
        p.setPen(QPen(color('dim' if is_major else 'line2'), 1))
        p.drawLine(QPointF(x, RULER - (11 if is_major else 5)), QPointF(x, RULER - 1))
        if is_major:
            p.setPen(color('muted'))
            p.drawText(QPointF(x + 4, 13), clock_label(t, major))


def draw_amplitude_grid(p, rect, mid, scale, width):
    """Lines at full scale, -6 dB and -12 dB on both sides of the middle, labelled in the gutter."""
    for v in (1.0, 0.5, 0.25, 0.0, -0.25, -0.5, -1.0):
        y = mid - v * scale
        p.setPen(QPen(color('line2' if v == 0 else 'line'), 1, Qt.PenStyle.SolidLine if v == 0 else Qt.PenStyle.DotLine))
        p.drawLine(QPointF(GUTTER, y), QPointF(width - RIGHT, y))
    p.setFont(theme.ui_font(10))
    p.setPen(color('dim'))
    for v, text in ((1.0, '0 dB'), (0.5, '-6'), (0.25, '-12'), (-0.25, '-12'), (-0.5, '-6'), (-1.0, '0 dB')):
        p.drawText(QRectF(0, mid - v * scale - 7, GUTTER - 8, 14),
                   int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter), text)


def polygon(xs, ys):
    return QPolygonF([QPointF(x, y) for x, y in zip(xs.tolist(), ys.tolist())])


def envelope_polygon(xs, hi, lo, mid, scale, floor=0.003):
    """A closed outline: the top edge from the maxima, then back along the bottom edge from the minima."""
    top = mid - np.maximum(hi, floor) * scale
    bottom = mid - np.minimum(lo, -floor) * scale
    return polygon(np.concatenate([xs, xs[::-1]]), np.concatenate([top, bottom[::-1]]))


def draw_edit(p, edit, rect, x_of, strong=True):
    """One operation on the master timeline: a cut is hatched red, silence grey, a fade a wedge, a gain amber."""
    a, b = edit['start_seconds'], edit['end_seconds']
    xa, xb = max(x_of(a), rect.left()), min(x_of(b), rect.right())
    if xb <= xa:
        return
    area = QRectF(xa, rect.top(), xb - xa, rect.height())
    op = edit.get('op', 'cut')
    p.setPen(Qt.PenStyle.NoPen)
    if op == 'cut':
        p.fillRect(area, color('red', 34 if strong else 46))
        p.fillRect(area, QBrush(color('red', 90 if strong else 110), Qt.BrushStyle.BDiagPattern))
        edge = color('red', 170)
    elif op == 'silence':
        p.fillRect(area, color('muted', 36))
        p.fillRect(area, QBrush(color('muted', 70), Qt.BrushStyle.Dense6Pattern))
        edge = color('muted', 150)
    elif op == 'fade':
        wedge = QPolygonF([QPointF(area.left(), area.bottom()), QPointF(area.right(), area.bottom()),
                           QPointF(area.right() if edit['direction'] == 'in' else area.left(), area.top())])
        p.setBrush(color('accent', 38))
        p.drawPolygon(wedge)
        edge = color('accent', 140)
    else:
        p.fillRect(area, color('amber', 30))
        edge = color('amber', 150)
        if strong and area.width() > 54:
            p.setFont(theme.ui_font(10, QFont.Weight.DemiBold))
            p.setPen(color('amber'))
            p.drawText(QRectF(area.left(), area.top() + 6, area.width(), 14), int(Qt.AlignmentFlag.AlignHCenter),
                       f"{edit['gain_db']:+.1f} dB")
    p.setPen(QPen(edge, 1))
    if x_of(a) >= rect.left():
        p.drawLine(area.topLeft(), area.bottomLeft())
    if x_of(b) <= rect.right():
        p.drawLine(area.topRight(), area.bottomRight())


# ---- the base of the waveform and the spectrogram ---------------------------------------------------------------------

class TimelineView(QWidget):
    """Time on the horizontal axis, a selection that can be dragged, a playhead, and the edits drawn over the clip."""

    selectRequested = Signal(float, float)
    seekRequested = Signal(float)
    clickRequested = Signal(float)
    contextRequested = Signal(object, float)

    ruler = 0                  # height of the ruler at the top of this view, if it has one

    def __init__(self, session, view, parent=None):
        super().__init__(parent)
        self.session = session
        self.view = view
        view.changed.connect(self.update)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        self._drag = None
        self._moved = False
        self._mouse_x = 0.0
        self._scroller = QTimer(self)
        self._scroller.setInterval(30)
        self._scroller.timeout.connect(self._autoscroll)

    # ---- geometry ----

    def plot_width(self):
        return max(1.0, self.width() - GUTTER - RIGHT)

    def x_of(self, t):
        return GUTTER + (t - self.view.t0) / self.view.span * self.plot_width()

    def t_of(self, x):
        return self.view.t0 + (x - GUTTER) / self.plot_width() * self.view.span

    def plot_rect(self):
        return QRectF(GUTTER, self.ruler, self.plot_width(), self.height() - self.ruler)

    def head(self):
        """The moment to draw the playhead at: the sound being heard while playing, else the edit cursor."""
        heard = self.session.transport.head
        return heard if heard is not None else self.session.editor.playhead

    def _clamped_time(self, x):
        return min(max(self.t_of(x), 0.0), self.view.seconds)

    # ---- overlays ----

    def paint_edits(self, p, rect, strong=True):
        take = self.session.current
        for edit in take.history.edits:
            draw_edit(p, edit, rect, self.x_of, strong)

    def paint_selection(self, p, rect, handles=True):
        editor = self.session.editor
        sel = editor.sel
        if sel is None:
            for point in (editor.in_point, editor.out_point):                          # a lone marker, before the pair
                if point is not None:
                    self._marker(p, self.x_of(point))
            return
        a, b = sel
        xa, xb = self.x_of(a), self.x_of(b)
        area = QRectF(max(xa, rect.left()), rect.top(), min(xb, rect.right()) - max(xa, rect.left()), rect.height())
        if area.width() > 0:
            p.fillRect(area, color('accent', 40))
        p.setPen(QPen(color('accent'), 1.5))
        mid = rect.center().y()
        for x in (xa, xb):
            if rect.left() - 1 <= x <= rect.right() + 1:
                p.drawLine(QPointF(x, rect.top()), QPointF(x, rect.bottom()))
                if handles:
                    p.setPen(Qt.PenStyle.NoPen)
                    p.setBrush(color('accent'))
                    p.drawRoundedRect(QRectF(x - 3.5, mid - 15, 7, 30), 3.5, 3.5)
                    p.setPen(QPen(color('accent'), 1.5))
        if handles and area.width() > 76:
            pill = QRectF(area.center().x() - 34, rect.top() + 8, 68, 20)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(color('accent'))
            p.drawRoundedRect(pill, 10, 10)
            p.setPen(color('ink'))
            p.setFont(theme.ui_font(11, QFont.Weight.DemiBold))
            p.drawText(pill, int(Qt.AlignmentFlag.AlignCenter), f'{b - a:.3f} s')

    def _marker(self, p, x):
        if GUTTER <= x <= self.width() - RIGHT and self.ruler:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(color('accent'))
            p.drawPolygon(QPolygonF([QPointF(x, self.ruler - 1), QPointF(x - 5, self.ruler - 11),
                                     QPointF(x + 5, self.ruler - 11)]))

    def paint_playhead(self, p):
        x = self.x_of(self.head())
        if GUTTER - 1 <= x <= self.width() - RIGHT + 1:
            p.setPen(QPen(color('text'), 1.5))
            p.drawLine(QPointF(x, 0), QPointF(x, self.height()))
            if self.ruler:
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(color('text'))
                p.drawPolygon(QPolygonF([QPointF(x - 6, 0), QPointF(x + 6, 0), QPointF(x, 10)]))

    # ---- the pointer ----

    def _edge_at(self, x):
        """0 or 1 when the pointer is on the start or end of the selection, else None."""
        sel = self.session.editor.sel
        if sel is None:
            return None
        near = [(abs(x - self.x_of(t)), i) for i, t in enumerate(sel) if abs(x - self.x_of(t)) <= EDGE_GRAB]
        return min(near)[1] if near else None

    def mousePressEvent(self, e):
        if self.session.current is None:
            return
        x, y = e.position().x(), e.position().y()
        if e.button() == Qt.MouseButton.RightButton:
            self.contextRequested.emit(e.globalPosition().toPoint(), self._clamped_time(x))
            return
        if e.button() != Qt.MouseButton.LeftButton:
            return
        self.setFocus()
        self._moved = False
        self._mouse_x = x
        if y < self.ruler:                              # the ruler scrubs the playhead
            self._drag = ('scrub',)
            self.seekRequested.emit(self._clamped_time(x))
            return
        edge = self._edge_at(x)
        if edge is not None:
            self._drag = ('edge', self.session.editor.sel[1 - edge])
        else:
            self._drag = ('new', self._clamped_time(x), x)

    def mouseMoveEvent(self, e):
        x, y = e.position().x(), e.position().y()
        self._mouse_x = x
        if self._drag is None:
            self._hover(x, y)
            return
        kind = self._drag[0]
        if kind == 'scrub':
            self.seekRequested.emit(self._clamped_time(x))
        elif kind == 'edge':
            self._moved = True
            self.selectRequested.emit(self._drag[1], self._clamped_time(x))
        else:
            if not self._moved and abs(x - self._drag[2]) <= CLICK_SLOP:
                return
            self._moved = True
            self.selectRequested.emit(self._drag[1], self._clamped_time(x))
        if kind != 'scrub' and not (GUTTER <= x <= self.width() - RIGHT) and not self._scroller.isActive():
            self._scroller.start()

    def mouseReleaseEvent(self, e):
        drag, self._drag = self._drag, None
        self._scroller.stop()
        if drag is not None and drag[0] == 'new' and not self._moved:
            self.clickRequested.emit(drag[1])
        self._moved = False

    def _hover(self, x, y):
        if self.session.current is None:
            self.unsetCursor()
        elif y < self.ruler:
            self.setCursor(Qt.CursorShape.PointingHandCursor)
        elif self._edge_at(x) is not None:
            self.setCursor(Qt.CursorShape.SizeHorCursor)
        else:
            self.setCursor(Qt.CursorShape.CrossCursor)

    def _autoscroll(self):
        """While a drag is held beyond either end of the plot, scroll that way and extend the selection to the edge."""
        if self._drag is None or self._drag[0] == 'scrub':
            self._scroller.stop()
            return
        left, right = GUTTER, self.width() - RIGHT
        over = self._mouse_x - right if self._mouse_x > right else self._mouse_x - left if self._mouse_x < left else 0.0
        if not over:
            self._scroller.stop()
            return
        self.view.pan_by(self.view.span * 0.03 * max(-3.0, min(3.0, over / 40.0)))
        t = min(max(self.view.t1 if over > 0 else self.view.t0, 0.0), self.view.seconds)
        anchor = self._drag[1]
        self.selectRequested.emit(anchor, t)

    def wheelEvent(self, e):
        if self.session.current is None:
            return
        delta = e.angleDelta()
        horizontal = bool(e.modifiers() & Qt.KeyboardModifier.ShiftModifier) or (delta.x() and not delta.y())
        steps = (delta.x() if delta.x() and not delta.y() else delta.y()) / 120.0
        if horizontal:
            self.view.pan_by(-steps * self.view.span * 0.1)
        else:
            x = e.position().x()
            anchor = self.t_of(x) if GUTTER <= x <= self.width() - RIGHT else (self.view.t0 + self.view.t1) / 2
            self.view.zoom_by(1.25 ** steps, anchor)
        e.accept()

    def keyPressEvent(self, e):
        if self.session.current is None:
            return super().keyPressEvent(e)
        modifiers = e.modifiers()
        step = 1.0 if modifiers & Qt.KeyboardModifier.ControlModifier else \
            0.1 if modifiers & Qt.KeyboardModifier.ShiftModifier else 0.01
        key = e.key()
        if key == Qt.Key.Key_Left:
            self.seekRequested.emit(max(self.head() - step, 0.0))
        elif key == Qt.Key.Key_Right:
            self.seekRequested.emit(min(self.head() + step, self.view.seconds))
        elif key == Qt.Key.Key_Home:
            self.seekRequested.emit(0.0)
        elif key == Qt.Key.Key_End:
            self.seekRequested.emit(self.view.seconds)
        else:
            return super().keyPressEvent(e)
        e.accept()


# ---- the waveform -----------------------------------------------------------------------------------------------------

class WaveformView(TimelineView):
    """The take's master as a peak envelope with the RMS body inside it, over a ruler and a dB grid.

    The waveform is drawn once into a cached layer and reused while only the selection, playhead or edits change, so
    dragging a selection costs a few rectangles, not a redraw of every column.
    """

    ruler = RULER

    def __init__(self, session, view, parent=None):
        super().__init__(session, view, parent)
        self._layer = None
        self._layer_key = None
        self.layer_builds = 0
        self.setMinimumHeight(200)

    def _geometry(self):
        area = self.height() - RULER
        return RULER + area / 2, area / 2 * 0.90

    def _layer_for(self, take):
        pyramid = self.session.measure.pyramid(take)
        dpr = self.devicePixelRatioF()
        key = (take.id, id(pyramid), round(self.view.t0, 6), round(self.view.t1, 6), self.width(), self.height(), dpr)
        if key == self._layer_key:
            return self._layer
        layer = QPixmap(max(1, int(self.width() * dpr)), max(1, int(self.height() * dpr)))
        layer.setDevicePixelRatio(dpr)
        layer.fill(Qt.GlobalColor.transparent)
        p = QPainter(layer)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        mid, scale = self._geometry()
        count = int(min(self.plot_width() * dpr, MAX_COLUMNS))
        i0, i1 = int(self.view.t0 * take.rate), int(self.view.t1 * take.rate)
        lo, hi = pyramid.columns(i0, i1, count)
        body = pyramid.body(i0, i1, count)
        xs = GUTTER + (np.arange(count) + 0.5) * self.plot_width() / count
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(color('accent', 95))
        p.drawPolygon(envelope_polygon(xs, hi, lo, mid, scale))
        p.setBrush(color('accent', 235))
        p.drawPolygon(envelope_polygon(xs, body, -body, mid, scale))
        self._paint_clipped(p, take, i0, i1)
        p.end()
        self._layer, self._layer_key = layer, key
        self.layer_builds += 1
        return layer

    def _paint_clipped(self, p, take, i0, i1):
        """Red ticks where samples reach full scale, and the word CLIP beside the first."""
        hits = np.flatnonzero(np.abs(take.mono[max(i0, 0):i1]) >= ac.CLIP_FLAG)
        if not hits.size:
            return
        times = (hits + max(i0, 0)) / take.rate
        for t in np.unique(np.round(times * 40) / 40):
            p.fillRect(QRectF(self.x_of(t) - 1.5, RULER + 2, 3, 9), color('red'))
        x = self.x_of(times[0])
        p.setPen(color('red'))
        p.setFont(theme.ui_font(10, QFont.Weight.Bold))
        p.drawText(QPointF(x + 6 if x + 44 < self.width() else x - 34, RULER + 11), 'CLIP')

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.fillRect(self.rect(), color('bg0'))
        take = self.session.current
        if take is None or self.view.seconds <= 0:
            return
        mid, scale = self._geometry()
        rect = self.plot_rect()
        draw_amplitude_grid(p, rect, mid, scale, self.width())
        self.paint_edits(p, rect)
        p.drawPixmap(0, 0, self._layer_for(take))
        draw_ruler(p, self.width(), self.view.t0, self.view.t1, self.x_of)
        self.paint_selection(p, rect)
        self.paint_playhead(p)


# ---- the spectrogram --------------------------------------------------------------------------------------------------

MAGMA = [(0.00, (18, 17, 15)), (0.16, (40, 17, 72)), (0.36, (104, 30, 128)), (0.56, (186, 54, 112)),
         (0.78, (248, 128, 62)), (1.00, (255, 236, 172))]
DYNAMIC_RANGE_DB = 66.0


def colormap(values):
    """Map values in 0 to 1 onto the dark-violet-to-orange scale; returns uint8 rows of RGB."""
    stops = np.array([s[0] for s in MAGMA])
    out = np.zeros(values.shape + (3,), dtype=np.uint8)
    for channel in range(3):
        out[..., channel] = np.interp(values, stops, [s[1][channel] for s in MAGMA])
    return out


def spectrogram_image(spec):
    """The spectrogram as a QImage: time left to right, low frequencies at the bottom, the loudest bin at the top of
    the scale and everything more than 66 dB below it black."""
    db = spec.db
    norm = np.clip((db - (float(db.max()) - DYNAMIC_RANGE_DB)) / DYNAMIC_RANGE_DB, 0.0, 1.0)[::-1]
    rgb = np.ascontiguousarray(colormap(norm))
    height, width, _ = rgb.shape
    return QImage(rgb.data, width, height, 3 * width, QImage.Format.Format_RGB888).copy()


class SpectrogramView(TimelineView):
    """Frequency against time, aligned with the waveform above it. Computed in the background, so it can arrive late."""

    def __init__(self, session, view, parent=None):
        super().__init__(session, view, parent)
        self.setMinimumHeight(110)

    def _image(self, take):
        spec = self.session.measure.spectrogram(take)
        if spec is None:
            return None, None
        if 'spectrogram_image' not in take.cache:
            take.cache['spectrogram_image'] = spectrogram_image(spec)
        return spec, take.cache['spectrogram_image']

    def paintEvent(self, event):
        p = QPainter(self)
        p.fillRect(self.rect(), color('bg0'))
        take = self.session.current
        if take is None or self.view.seconds <= 0:
            return
        spec, image = self._image(take)
        rect = self.plot_rect()
        if image is None:
            p.setPen(color('dim'))
            p.setFont(theme.ui_font(12))
            p.drawText(rect, int(Qt.AlignmentFlag.AlignCenter), 'Analyzing the spectrogram...')
        else:
            frames = image.width()
            src = QRectF(self.view.t0 / spec.hop_seconds, 0, self.view.span / spec.hop_seconds, image.height())
            src = src.intersected(QRectF(0, 0, frames, image.height()))
            target = QRectF(self.x_of(src.left() * spec.hop_seconds), 0,
                            src.width() * spec.hop_seconds / self.view.span * self.plot_width(), self.height())
            p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
            p.drawImage(target, image, src)
            self._paint_axis(p, spec, image)
        self.paint_edits(p, rect, strong=False)
        self.paint_selection(p, rect, handles=False)
        self.paint_playhead(p)

    def _paint_axis(self, p, spec, image):
        top_hz = (image.height() - 1) * spec.bin_hz
        h = self.height()
        p.setFont(theme.ui_font(10))
        for khz in range(0, int(top_hz // 1000) + 1, 2):
            y = h - khz * 1000 / top_hz * h
            if 0 < khz * 1000 < top_hz - 500:
                p.setPen(QPen(color('#ffffff', 26), 1, Qt.PenStyle.DotLine))
                p.drawLine(QPointF(GUTTER, y), QPointF(self.width() - RIGHT, y))
            p.setPen(color('dim'))
            p.drawText(QRectF(0, min(max(y - 7, 0), h - 14), GUTTER - 8, 14),
                       int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter), f'{khz} kHz' if khz else '0')


# ---- the overview -----------------------------------------------------------------------------------------------------

class Minimap(QWidget):
    """The whole clip in one strip, with the visible part boxed. Drag the box to scroll, click outside it to jump."""

    def __init__(self, session, view, parent=None):
        super().__init__(parent)
        self.session = session
        self.view = view
        view.changed.connect(self.update)
        self.setFixedHeight(58)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._offset = None
        self._layer = None
        self._layer_key = None

    def _plot(self):
        return QRectF(GUTTER, 6, max(1.0, self.width() - GUTTER - RIGHT), self.height() - 12)

    def _t_of(self, x):
        plot = self._plot()
        return (x - plot.left()) / plot.width() * self.view.seconds

    def _x_of(self, t):
        plot = self._plot()
        return plot.left() + t / self.view.seconds * plot.width()

    def mousePressEvent(self, e):
        if self.session.current is None or e.button() != Qt.MouseButton.LeftButton:
            return
        t = self._t_of(e.position().x())
        if not self.view.t0 <= t <= self.view.t1:                  # outside the box: centre it there first
            self.view.set(t - self.view.span / 2, t + self.view.span / 2)
        self._offset = t - self.view.t0

    def mouseMoveEvent(self, e):
        if self._offset is not None:
            start = self._t_of(e.position().x()) - self._offset
            self.view.set(start, start + self.view.span)

    def mouseReleaseEvent(self, e):
        self._offset = None

    def mouseDoubleClickEvent(self, e):
        self.view.fit()

    def _layer_for(self, take):
        dpr = self.devicePixelRatioF()
        pyramid = self.session.measure.pyramid(take)
        key = (take.id, id(pyramid), self.width(), self.height(), dpr)
        if key == self._layer_key:
            return self._layer
        layer = QPixmap(max(1, int(self.width() * dpr)), max(1, int(self.height() * dpr)))
        layer.setDevicePixelRatio(dpr)
        layer.fill(Qt.GlobalColor.transparent)
        p = QPainter(layer)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        plot = self._plot()
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(color('bg2'))
        p.drawRoundedRect(plot, 7, 7)
        count = int(min(plot.width() * dpr, MAX_COLUMNS))
        lo, hi = pyramid.columns(0, pyramid.n, count)
        xs = plot.left() + (np.arange(count) + 0.5) * plot.width() / count
        p.setBrush(color('muted', 150))
        p.drawPolygon(envelope_polygon(xs, hi, lo, plot.center().y(), plot.height() / 2 * 0.85, 0.004))
        p.end()
        self._layer, self._layer_key = layer, key
        return layer

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        take = self.session.current
        if take is None or self.view.seconds <= 0:
            return
        p.drawPixmap(0, 0, self._layer_for(take))
        plot = self._plot()
        for edit in take.history.edits:
            if edit.get('op', 'cut') == 'cut':
                a, b = self._x_of(edit['start_seconds']), self._x_of(edit['end_seconds'])
                p.fillRect(QRectF(a, plot.top(), max(2.0, b - a), plot.height()), color('red', 90))
        sel = self.session.editor.sel
        if sel is not None:
            a, b = self._x_of(sel[0]), self._x_of(sel[1])
            p.fillRect(QRectF(a, plot.top(), max(2.0, b - a), plot.height()), color('accent', 70))
        box = QRectF(self._x_of(self.view.t0), plot.top(), self._x_of(self.view.t1) - self._x_of(self.view.t0),
                     plot.height())
        p.setBrush(color('accent', 36))
        p.setPen(QPen(color('accent'), 1.5))
        p.drawRoundedRect(box, 5, 5)
        head = self._x_of(min(self.session.transport.head if self.session.transport.head is not None
                              else self.session.editor.playhead, self.view.seconds))
        p.setPen(QPen(color('text'), 1.2))
        p.drawLine(QPointF(head, plot.top() + 1), QPointF(head, plot.bottom() - 1))


# ---- the live view while recording -----------------------------------------------------------------------------------

class LiveWaveView(QWidget):
    """What is being recorded, drawn as it arrives, with a REC badge and a red line at the newest sound."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.rows = np.zeros((0, 3), dtype=np.float32)
        self.elapsed = 0.0
        self.setMinimumHeight(200)

    def set_state(self, rows, elapsed):
        self.rows = rows if rows is not None else np.zeros((0, 3), dtype=np.float32)
        self.elapsed = float(elapsed)
        self.update()

    def window_end(self):
        """The right edge of the picture in seconds: 10 s, then growing in steps of 5 s up to the 40 s limit."""
        return min(max(10.0, math.ceil((self.elapsed + 1.0) / 5.0) * 5.0), ac.MAX_SECONDS)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.fillRect(self.rect(), color('bg0'))
        w, h = self.width(), self.height()
        plot_w = max(1.0, w - GUTTER - RIGHT)
        end = self.window_end()

        def x_of(t):
            return GUTTER + t / end * plot_w

        mid, scale = RULER + (h - RULER) / 2, (h - RULER) / 2 * 0.90
        draw_amplitude_grid(p, QRectF(GUTTER, RULER, plot_w, h - RULER), mid, scale, w)
        if len(self.rows):
            xs = x_of(self.rows[:, 0].astype(np.float64))
            pixel = np.floor(xs).astype(np.int64)
            first = np.flatnonzero(np.diff(pixel, prepend=pixel[0] - 1))
            lo = np.minimum.reduceat(self.rows[:, 1], first)
            hi = np.maximum.reduceat(self.rows[:, 2], first)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(color('accent', 235))
            p.drawPolygon(envelope_polygon(xs[first], hi, lo, mid, scale))
        draw_ruler(p, w, 0.0, end, x_of)
        x = x_of(min(self.elapsed, end))
        p.setPen(QPen(color('red'), 2))
        p.drawLine(QPointF(x, RULER), QPointF(x, h))
        badge = QRectF(GUTTER + 12, RULER + 12, 62, 22)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(color('red', 40))
        p.drawRoundedRect(badge, 11, 11)
        p.setBrush(color('red'))
        p.drawEllipse(QPointF(badge.left() + 13, badge.center().y()), 4, 4)
        p.setPen(color('red'))
        p.setFont(theme.ui_font(11, QFont.Weight.Bold))
        p.drawText(QRectF(badge.left() + 22, badge.top(), 38, 22),
                   int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter), 'REC')
