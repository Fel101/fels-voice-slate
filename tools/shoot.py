"""Render every state of the window to PNG with synthetic audio, so the look can be checked and the README illustrated.

    python tools/shoot.py [--out DIR] [--scale 1.5] [--size 1440x960] [--only edit process ...]

It needs no microphone, speaker or recording: the take is generated (tools/synthetic.py) and the library is a temporary
folder that is removed afterwards. Run it with the same Python as the recorder.

The pictures come out the same size on every display (1440 by 960 by default, whatever the display's own scaling is);
--scale makes them larger.
"""
import argparse
import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

STATES = ['edit', 'process', 'check', 'analyze', 'record', 'empty', 'compact', 'settings', 'about', 'shortcuts', 'copy',
          'manifest']
SAMPLE = ('The morning ferry left the harbour just as the fog began to lift from the water, and the gulls followed '
          'it out past the lighthouse.')
SCRIPT = ('The morning ferry left the harbour just as the fog began to lift.\n'
          'A few gulls followed it out past the lighthouse.\n'
          'By noon the water was calm and the sky was clear.\n'
          'Nobody on the quay said a word until it was gone.')


def parse():
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('--out', default=str(ROOT / 'docs' / 'images'), help='folder for the PNG files')
    parser.add_argument('--scale', type=float, default=1.0, help='display scale, for example 1.5 for a sharper picture')
    parser.add_argument('--size', default='1440x960', help='window size, WIDTHxHEIGHT')
    parser.add_argument('--only', nargs='*', choices=STATES, help='render only these states')
    return parser.parse_args()


def build_session(work):
    from recorder import capture, settings
    from recorder.jobs import Jobs
    from recorder.session import Session
    from recorder.store import Library
    import synthetic

    capture.list_microphones = lambda: [(1, 'Studio Microphone  [Windows WASAPI]'), (2, 'Headset Microphone  [MME]')]
    capture.default_microphone = lambda: 1
    capture.list_loopbacks = lambda: [(9, 'Speakers [Loopback]')]
    capture.default_loopback = lambda: 9
    settings_file = work / 'settings.json'
    settings.update({'root': str(work / 'clips')}, settings_file)
    session = Session(settings_file=settings_file, jobs=Jobs(inline=True), player=synthetic.NullPlayer(),
                      library=Library(work / 'clips', trash=lambda paths: None))
    return session


def hurried(rate):
    """The take as someone in a hurry might record it: quiet, with a long wait before the first word and a long pause."""
    import numpy as np

    import synthetic
    x = synthetic.take(14.0, level=0.4, noise=0.0006)
    room = np.tile(x[:int(0.45 * rate)], 8)                  # the room's own noise and hum, to fill the extra silences
    pause = int(4.5 * rate)
    return np.concatenate([room[:int(2.0 * rate)], x[:pause], room[:int(1.4 * rate)], x[pause:]])


def seed(session, rough=False):
    """A speaker with four takes: one imported and kept, one from system audio, one kept, and the one being reviewed."""
    import numpy as np

    import synthetic
    workspace, rate = session.workspace, synthetic.RATE
    workspace.add_speaker('My voice')
    x = synthetic.take(14.0)
    older = [('import', np.roll(x, 150000)[:int(13.6 * rate)], True), ('system', np.roll(x, 90000)[:int(9.8 * rate)], False),
             ('mic', np.roll(x, 40000)[:int(11.2 * rate)], True)]
    for source, samples, keep in older:
        workspace.add_take(samples, rate, source, {})
        if keep:
            workspace.set_meta(transcript=SAMPLE, origin='Recorded by me, in my room, microphone only',
                               allowed_use='Personal voice projects', consent_confirmed=True)
            session.keep()
    workspace.add_take(hurried(rate) if rough else x, rate, 'mic', {})
    return x


def settle(app, session, rounds=8):
    for _ in range(rounds):
        session.tick()
        app.processEvents()


def make_window(app, session):
    from recorder.ui.main_window import MainWindow
    window = MainWindow(session=session)
    window.timer.stop()                      # the pictures are taken by hand; nothing should move underneath them
    return window


def measure(app, window):
    """Run the measurements for the current recipe now, instead of after the usual 200 ms wait."""
    window._analysis_timer.stop()
    window._request_analysis()
    for _ in range(30):
        settle(app, window.session, rounds=2)
        if window._derived() is not None:
            break
    window.inspector.refresh_analysis(window._derived())


def review_take(app, window):
    """The take being reviewed: a draft transcript, statements entered, permission not yet ticked, one cut."""
    session = window.session
    take = session.current
    session.drafts.receive(take, SAMPLE, None)
    session.workspace.set_meta(origin='Recorded by me, in my room, microphone only', allowed_use='Personal voice projects')
    session.editor.set_selection(7.30, 7.62)
    session.editor.cut_selection()
    session.workspace.confirm_transcript()
    session.editor.set_selection(4.62, 5.43)
    session.editor.set_playhead(6.35)
    window.flush()
    settle(app, session)
    measure(app, window)
    window.editor_panel.view.set(1.0, 11.0)
    window._show_status('Cut recorded. The master file is unchanged.')
    return take


def analyze_take(app, window):
    """The hurried take with Auto analyze pressed: the Check tab, opened on what could be better and what Auto fix would do."""
    session = window.session
    session.drafts.receive(session.current, SAMPLE, None)
    session.workspace.set_meta(origin='Recorded by me, in my room, microphone only', allowed_use='Personal voice projects')
    session.workspace.confirm_transcript()
    window.flush()
    settle(app, session)
    measure(app, window)
    window.actions.auto_analyze.trigger()
    window.flush()
    settle(app, session)
    window.flush()


def save_picture(widget, path, scale):
    """Save a picture of the widget, `scale` times its size in logical pixels whatever the display's own scaling is."""
    from PySide6.QtCore import Qt
    picture = widget.grab()
    wanted = (round(widget.width() * scale), round(widget.height() * scale))
    if (picture.width(), picture.height()) != wanted:
        picture = picture.scaled(*wanted, Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation)
    picture.save(str(path))
    print('wrote', path)


def render(window, app, path, size, scale):
    from PySide6.QtCore import Qt
    window.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
    window.resize(*size)
    window.show()
    for _ in range(6):
        app.processEvents()
    save_picture(window, path, scale)


def main():
    args = parse()
    size = tuple(int(v) for v in args.size.lower().split('x'))
    os.environ['QT_SCALE_FACTOR'] = str(args.scale)
    from PySide6.QtWidgets import QApplication

    from recorder.ui import theme
    theme.prepare_display()
    app = QApplication(sys.argv[:1])
    theme.install(app)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    wanted = args.only or STATES
    work = Path(tempfile.mkdtemp(prefix='recorder-shoot-'))
    try:
        for state in wanted:
            folder = work / state
            folder.mkdir()
            session = build_session(folder)
            if state in ('settings', 'about', 'shortcuts', 'copy', 'manifest'):
                from PySide6.QtCore import Qt

                from recorder import clean
                from recorder.ui import dialogs
                clean.locate = lambda configured=None: Path(r'C:\Tools\ffmpeg\bin\ffmpeg.exe')   # no real paths in pictures
                fake = {'root': r'C:\Apps\voice-slate\voice-clips', 'asr_language': 'en', 'tail_seconds': 0.5}
                dialog = {'settings': lambda: dialogs.SettingsDialog(fake),
                          'about': dialogs.AboutDialog, 'shortcuts': dialogs.ShortcutsDialog,
                          'copy': lambda: dialogs.CopyDialog('Welcome back, take two'),
                          'manifest': lambda: dialogs.ManifestDialog('My voice')}[state]()
                dialog.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)        # drawn, but never on the person's screen
                dialog.show()
                for _ in range(4):
                    app.processEvents()
                save_picture(dialog, out / f'{state}.png', args.scale)
                dialog.close()
                continue
            if state == 'empty':
                window = make_window(app, session)
                render(window, app, out / 'empty.png', size, args.scale)
                window.close()
                continue
            seed(session, rough=state == 'analyze')
            window = make_window(app, session)
            window.flush()
            settle(app, session)
            if state == 'record':
                import synthetic
                from recorder import capture
                capture.open_capture = lambda source, device, pre_roll: synthetic.SyntheticCapture(12.48)
                session.script.load_text(SCRIPT)
                session.script.step(2)
                session.capturing.start()
                window.flush()
                window._tick()
                window.timer.stop()
                window._show_status('Recording. Press F9 or Stop when finished.')
                render(window, app, out / 'record.png', size, args.scale)
            elif state == 'analyze':
                analyze_take(app, window)
                render(window, app, out / 'analyze.png', size, args.scale)
            else:
                review_take(app, window)
                if state == 'process':
                    session.editor.set_chain('highpass', 'High-pass on', enabled=True, hz=80.0)
                    session.editor.set_chain('limiter', 'Limiter on', enabled=True)
                    session.editor.set_chain('level', 'Gain', gain_db=5.1)
                    window.flush()
                    measure(app, window)
                    window.inspector.show_tab(1)
                elif state == 'check':
                    window.inspector.show_tab(2)
                render(window, app, out / f'{state}.png', (1100, 700) if state == 'compact' else size, args.scale)
            window.timer.stop()
            window.close()
    finally:
        shutil.rmtree(work, ignore_errors=True)
    # Let Qt delete the windows and dialogs now, while the interpreter is whole: left for its shutdown, the teardown
    # sometimes aborts, after every picture has been written.
    from PySide6.QtCore import QEvent
    for widget in app.topLevelWidgets():
        widget.close()
        widget.deleteLater()
    app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    app.processEvents()


if __name__ == '__main__':
    main()
