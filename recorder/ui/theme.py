"""The look of the window: the stylesheet, the fonts, and the few images the stylesheet needs."""
import atexit
import shutil
import tempfile
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QFontDatabase
from PySide6.QtWidgets import QApplication

from . import icons
from .palette import PALETTE

UI_FAMILIES = ['Segoe UI', 'Segoe UI Variable Text', 'Inter', 'Helvetica Neue', 'Arial']
MONO_FAMILIES = ['Cascadia Mono', 'Cascadia Code', 'Consolas', 'Menlo', 'Courier New']
BASE_PIXELS = 13

QSS = """
QWidget { color: @text@; background: transparent; }
QMainWindow, QDialog, QMessageBox, QWidget#root { background: @bg0@; }
QWidget#topbar { background: @bg1@; border-bottom: 1px solid @line@; }
QWidget#library, QWidget#inspector { background: @bg1@; }
QWidget#library { border-right: 1px solid @line@; }
QWidget#inspector { border-left: 1px solid @line@; }
QStatusBar { background: @bg1@; border-top: 1px solid @line@; }
QStatusBar::item { border: none; }
QFrame#card { background: @bg2@; border: 1px solid @line@; border-radius: 10px; }
QFrame#editbar { background: @bg1@; border: 1px solid @line@; border-radius: 10px; }
QFrame#script { background: @bg2@; border: 1px solid @line2@; border-radius: 12px; }
QFrame#empty { background: @bg1@; border: 1px dashed @line2@; border-radius: 14px; }
QLabel[role="muted"] { color: @muted@; }
QLabel[role="dim"] { color: @dim@; }
QPushButton { background: @bg3@; border: 1px solid @line2@; border-radius: 7px; padding: 6px 12px; }
QPushButton:hover { background: @bg4@; }
QPushButton:pressed { background: #3f3b34; }
QPushButton:disabled { color: @dim@; background: @bg2@; border-color: @line@; }
QPushButton:focus { border: 1px solid @accent_d@; }
QPushButton[kind="primary"] { background: @accent@; color: @ink@; border: none; font-weight: 600; padding: 9px 16px; }
QPushButton[kind="primary"]:hover { background: #c3a3ff; }
QPushButton[kind="primary"]:disabled { background: #3a3350; color: #a39bb8; }
QPushButton[kind="record"] { background: @red@; color: #230d0b; border: none; font-weight: 700; border-radius: 21px;
                             padding: 0 20px; }
QPushButton[kind="record"]:hover { background: #ff8279; }
QPushButton[kind="recording"] { background: rgba(255,106,95,30); color: @red@; border: 1.5px solid @red@;
                                font-weight: 700; border-radius: 21px; padding: 0 20px; }
QPushButton[kind="ghost"] { background: transparent; border: 1px solid transparent; color: @muted@; }
QPushButton[kind="ghost"]:hover { background: @bg3@; color: @text@; }
QPushButton[kind="ghost"]:disabled { color: @dim@; background: transparent; }
QPushButton[kind="danger"] { background: transparent; border: 1px solid transparent; color: @muted@; }
QPushButton[kind="danger"]:hover { background: rgba(255,106,95,30); color: @red@; }
QPushButton[kind="link"] { background: transparent; border: none; color: @accent@; padding: 0 2px; }
QPushButton[kind="link"]:hover { color: #c3a3ff; text-decoration: underline; }
QPushButton[seg="on"] { background: rgba(177,140,255,44); color: @accent@; border: 1px solid rgba(177,140,255,110); }
QPushButton[seg="off"] { background: @bg2@; color: @muted@; }
QPushButton[seg="off"]:hover { background: @bg3@; color: @text@; }
QPushButton[seg="off"]:disabled, QPushButton[seg="on"]:disabled { color: @dim@; }
QToolButton { background: transparent; border: 1px solid transparent; border-radius: 7px; padding: 5px 8px; }
QToolButton:hover { background: @bg3@; }
QToolButton:pressed { background: @bg4@; }
QToolButton:checked { background: rgba(177,140,255,44); color: @accent@; border: 1px solid rgba(177,140,255,110); }
QToolButton:disabled { color: @dim@; }
QToolButton::menu-indicator { image: none; }
QLineEdit, QPlainTextEdit, QAbstractSpinBox { background: @bg2@; border: 1px solid @line2@; border-radius: 7px;
                                               padding: 6px 9px; selection-background-color: @accent_d@; }
QLineEdit:focus, QPlainTextEdit:focus, QAbstractSpinBox:focus { border: 1px solid @accent@; }
QLineEdit:disabled, QAbstractSpinBox:disabled { color: @dim@; background: @bg1@; }
QAbstractSpinBox[flat="true"] { background: transparent; border: 1px solid transparent; padding: 2px 4px; }
QAbstractSpinBox[flat="true"]:hover { background: @bg2@; border: 1px solid @line2@; }
QAbstractSpinBox[flat="true"]:focus { background: @bg2@; border: 1px solid @accent@; }
QAbstractSpinBox::up-button, QAbstractSpinBox::down-button { width: 0; border: none; }
QComboBox { background: @bg3@; border: 1px solid @line2@; border-radius: 7px; padding: 6px 10px; min-height: 18px; }
QComboBox:hover { background: @bg4@; }
QComboBox:disabled { color: @dim@; background: @bg2@; }
QComboBox::drop-down { border: none; width: 26px; }
QComboBox::down-arrow { image: url(@chevron@); width: 14px; height: 14px; }
QComboBox QAbstractItemView { background: @bg2@; border: 1px solid @line2@; selection-background-color: @accent_d@;
                              outline: none; }
QCheckBox { spacing: 10px; }
QCheckBox::indicator { width: 18px; height: 18px; border-radius: 5px; border: 1.5px solid @dim@; background: @bg2@; }
QCheckBox::indicator:hover { border-color: @muted@; }
QCheckBox::indicator:checked { background: @accent@; border-color: @accent@; image: url(@check@); }
QSlider::groove:horizontal { height: 4px; background: @bg4@; border-radius: 2px; }
QSlider::sub-page:horizontal { background: @accent@; border-radius: 2px; }
QSlider::sub-page:horizontal:disabled { background: @dim@; }
QSlider::handle:horizontal { background: @text@; width: 14px; height: 14px; margin: -5px 0; border-radius: 7px; }
QSlider::handle:horizontal:disabled { background: @dim@; }
QListWidget { background: @bg2@; border: 1px solid @line2@; border-radius: 8px; outline: 0; padding: 3px; }
QListWidget::item { padding: 5px 8px; border-radius: 5px; }
QListWidget::item:hover { background: @bg3@; }
QListWidget::item:selected { background: rgba(177,140,255,40); color: @text@; }
QScrollArea { border: none; }
QScrollBar:vertical { background: transparent; width: 10px; margin: 2px; }
QScrollBar::handle:vertical { background: @bg4@; border-radius: 3px; min-height: 30px; }
QScrollBar::handle:vertical:hover { background: @dim@; }
QScrollBar:horizontal { background: transparent; height: 10px; margin: 2px; }
QScrollBar::handle:horizontal { background: @bg4@; border-radius: 3px; min-width: 30px; }
QScrollBar::add-line, QScrollBar::sub-line { height: 0; width: 0; }
QScrollBar::add-page, QScrollBar::sub-page { background: transparent; }
QMenu { background: @bg2@; border: 1px solid @line2@; border-radius: 8px; padding: 6px; }
QMenu::item { padding: 7px 28px 7px 14px; border-radius: 5px; }
QMenu::item:selected { background: @bg4@; }
QMenu::item:disabled { color: @dim@; }
QMenu::separator { height: 1px; background: @line@; margin: 6px 8px; }
QToolTip { background: @bg3@; color: @text@; border: 1px solid @line2@; padding: 4px 8px; }
"""


def _find(candidates):
    families = set(QFontDatabase.families())
    return next((name for name in candidates if name in families), None)


def ui_family():
    return _find(UI_FAMILIES)


def mono_family():
    return _find(MONO_FAMILIES) or QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont).family()


def ui_font(pixels=BASE_PIXELS, weight=None, spacing=None):
    font = QFont()
    family = ui_family()
    if family:
        font.setFamily(family)
    font.setPixelSize(pixels)
    if weight is not None:
        font.setWeight(weight)
    if spacing is not None:
        font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, spacing)
    return font


def mono_font(pixels=BASE_PIXELS, weight=None):
    font = QFont(mono_family())
    font.setPixelSize(pixels)
    if weight is not None:
        font.setWeight(weight)
    return font


def _assets():
    """The two small images the stylesheet refers to, written to a temporary folder that is removed at exit."""
    folder = Path(tempfile.mkdtemp(prefix='voice-slate-ui-'))
    atexit.register(shutil.rmtree, folder, ignore_errors=True)
    chevron, check = folder / 'chevron.png', folder / 'check.png'
    icons.render('chevron_down', PALETTE['muted'], 14, scale=4).save(str(chevron))
    icons.render('check', PALETTE['ink'], 14, scale=4).save(str(check))
    return chevron.as_posix(), check.as_posix()


def stylesheet():
    chevron, check = _assets()
    sheet = QSS
    for key, value in PALETTE.items():
        sheet = sheet.replace(f'@{key}@', value)
    return sheet.replace('@chevron@', chevron).replace('@check@', check)


def install(app: QApplication):
    """Give the application the Fusion style, the base font and the stylesheet."""
    app.setStyle('Fusion')
    app.setFont(ui_font())
    app.setStyleSheet(stylesheet())
    app.setWindowIcon(icons.app_icon())


def prepare_display():
    """Per-monitor high-DPI scaling without rounding, so text stays sharp at 125% and 150%. Call before QApplication."""
    QApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
