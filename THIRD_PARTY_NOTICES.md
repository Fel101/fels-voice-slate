# Third-party notices

The code of Fel's Voice Slate itself is released under the [MIT License](LICENSE).

It **uses** the libraries below. They are not copied into this repository and are not modified: `pip` installs them
from PyPI (see [requirements.txt](requirements.txt)), and each one stays under its own licence. The full licence text of
every package is installed with it, in `<your environment>\Lib\site-packages\<package>-<version>.dist-info\licenses\`.

The versions in the table are the ones this release was built and tested with.

## Libraries the program needs

| Package | Version | Licence | Used for |
|---|---|---|---|
| [PySide6-Essentials](https://pyside.org) and shiboken6 (Qt for Python, Qt 6.12) | 6.12.0 | LGPL-3.0-only, GPL-2.0-only or GPL-3.0-only (this project uses it under **LGPL-3.0**) | The window |
| [sounddevice](https://github.com/spatialaudio/python-sounddevice) | 0.5.6 | MIT | Microphone input and playback. Its Windows package includes PortAudio |
| [PyAudioWPatch](https://github.com/s0d3s/PyAudioWPatch) | 0.2.12.9 | Apache-2.0 | Capturing what the computer is playing (WASAPI loopback) |
| [NumPy](https://numpy.org) | 2.5.4 | BSD-3-Clause, with bundled code under 0BSD, MIT, Zlib and CC0-1.0 | Audio arrays |
| [SciPy](https://scipy.org) | 1.18.1 | BSD-3-Clause, with bundled code under compatible licences | Filters, resampling and spectra |
| [SoundFile](https://github.com/bastibe/python-soundfile) | 0.14.0 | BSD-3-Clause. Its Windows package includes libsndfile (LGPL-2.1) and the codec libraries it uses | Reading and writing WAV files |
| [Send2Trash](https://github.com/arsenetar/send2trash) | 2.1.0 | BSD-3-Clause | Moving discarded takes to the Recycle Bin |

Installed alongside them because they need them: [cffi](https://cffi.readthedocs.io) 2.1.1 (MIT-0),
[pycparser](https://github.com/eliben/pycparser) 3.11 (BSD-3-Clause) and
[typing-extensions](https://github.com/python/typing_extensions) 4.16.0 (PSF-2.0).

### Qt and the LGPL

Qt for Python is used as an ordinary installed library: the program imports it from the `PySide6` package that `pip`
puts in your environment, which you can replace with another build of the same version. The program does not modify Qt.
The source code of Qt for Python is available from <https://code.qt.io/cgit/pyside/pyside-setup.git/> and Qt's from
<https://code.qt.io>.

This repository does not contain a packaged build. If you make one (for example an `.exe` with PyInstaller), you are
distributing Qt, and the LGPL-3.0 then asks you to ship its licence text, say that Qt is used, and let users replace the
Qt libraries. See the [Qt licensing page](https://www.qt.io/licensing/) and the licence texts in the installed packages.

## Tools for developing and testing

Only needed to run the tests and the checks (see [requirements-dev.txt](requirements-dev.txt)). Nothing here is needed
to run the program.

| Package | Version | Licence |
|---|---|---|
| [pytest](https://docs.pytest.org) | 9.1.1 | MIT |
| [pyflakes](https://github.com/PyCQA/pyflakes) | 4.0.3 | MIT |
| pluggy 1.7.0, iniconfig 2.3.1 | | MIT |
| packaging 26.3 | | Apache-2.0 or BSD-2-Clause |
| Pygments 2.21.0 | | BSD-2-Clause |
| colorama 0.4.6 | | BSD-3-Clause |

## Programs the recorder can call, which are not part of it

These are **optional**, you install them yourself, and the recorder runs each one as a separate program. No code or file
from them is included here, and the recorder never downloads them.

- [FFmpeg](https://ffmpeg.org): used for the noise reduction step (its `afftdn` filter). FFmpeg is licensed under the
  LGPL-2.1-or-later or the GPL-2.0-or-later, depending on how a build was made. Read the licence of the build you install.
- [faster-whisper](https://github.com/SYSTRAN/faster-whisper) (MIT) and a speech model for it: used for draft
  transcripts. The model is a separate download with its own licence, for example the Whisper models published by
  OpenAI under the MIT licence. Check the licence of the model you choose.

## Fonts, icons and sample material

- **Fonts:** none are included. The window uses the fonts Windows already has (Segoe UI and Cascadia Mono or Consolas)
  and falls back to other installed fonts.
- **Icons:** every icon is drawn in code as simple SVG geometry by this project (`recorder/ui/icons.py`). No third-party
  icon set or icon font was used. They are covered by this project's MIT licence.
- **Screenshots** in `docs/images` show generated, synthetic audio (`tools/synthetic.py`); no real person's voice is
  used. The sample sentences in them were written for this project.

## Trademarks

Qt, Windows, FFmpeg, Whisper and the other names above belong to their owners. They are used only to say what works
with this program. This project is not affiliated with or endorsed by any of them.
