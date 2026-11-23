"""Write the program's icon as recorder/resources/voice-slate.ico (and a 256 px PNG beside it).

    python tools/make_icon.py

The window and this tool draw recorder/resources/voice-slate.svg through the same renderer. The .ico supplies the
installer and shortcuts with PNG pictures at 16, 24, 32, 48, 64, 128 and 256 px. Run this after changing the SVG, and
commit the two files it writes.
"""
import os
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

OUT = ROOT / 'recorder' / 'resources'


def png_bytes(pixmap):
    from PySide6.QtCore import QBuffer, QByteArray, QIODevice

    data = QByteArray()
    buffer = QBuffer(data)
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    pixmap.save(buffer, 'PNG')
    buffer.close()
    return bytes(data)


def ico_bytes(pictures):
    """An .ico file holding PNG pictures: [(size, png bytes)], one directory entry each."""
    head = struct.pack('<HHH', 0, 1, len(pictures))
    offset = len(head) + 16 * len(pictures)
    entries, body = b'', b''
    for size, png in pictures:
        side = 0 if size >= 256 else size                   # the directory stores 256 as 0
        entries += struct.pack('<BBBBHHII', side, side, 0, 0, 1, 32, len(png), offset + len(body))
        body += png
    return head + entries + body


def main():
    from PySide6.QtWidgets import QApplication

    from recorder.ui import icons

    app = QApplication(sys.argv[:1])                        # a QPixmap needs an application
    pictures = [(size, png_bytes(icons.app_pixmap(size))) for size in icons.APP_ICON_SIZES]
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / 'voice-slate.ico').write_bytes(ico_bytes(pictures))
    (OUT / 'voice-slate.png').write_bytes(pictures[-1][1])
    print('wrote', OUT / 'voice-slate.ico', 'and', OUT / 'voice-slate.png')
    del app


if __name__ == '__main__':
    main()
