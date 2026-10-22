"""The main window: a thin view over a `Session`.

Nothing here decides anything. A click or a key calls a session method; the session reports what changed on its bus;
the window redraws the parts that heard about it. A timer calls `session.tick()` about twenty times a second, which
is also when the meter, the clock, the live waveform and the playhead are updated.
"""
import base64
from pathlib import Path

from PySide6.QtCore import QByteArray, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QApplication, QLabel, QMainWindow, QMenu, QWidget

from .. import audio_core as ac
from .. import clean, settings
from ..clean import CleanError
from ..core import render
from ..core.edits import describe
from ..session import Session
from ..session.measure import recipe_key
from ..store import KeepBlocked, TrashUnavailable
from .actions import SPEC, Actions, shortcut_text
from .dialogs import Dialogs
from .editor_panel import EditorPanel
from .inspector import Inspector
from .library import LibraryPanel
from .palette import PALETTE
from .topbar import TopBar
from .widgets import ElidedLabel, format_clock, hbox, label, vbox

MIN_SIZE = QSize(1100, 700)
PREFERRED_SIZE = QSize(1440, 900)
AUDIO_SUFFIXES = {'.wav', '.flac', '.ogg', '.opus', '.mp3', '.aif', '.aiff'}
TICK_MS = 50
ANALYSIS_DELAY_MS = 200            # how long the measurements wait after the last edit, so a dragged slider measures once
ALERT_MS = 2000                    # how long the taskbar button flashes when F9 was pressed in another program


class MainWindow(QMainWindow):
    closed = Signal()

    def __init__(self, session=None, dialogs=None, settings_file=None, hotkey=None):
        """`hotkey` is the key that reaches this window from other programs (recorder/hotkey.py); without one, F9 only
        works while the window is in front. `run()` supplies it, so no test or tool takes F9 from the machine it runs on."""
        super().__init__()
        self.session = session or Session(settings_file=settings_file)
        self.dialogs = dialogs or Dialogs(self)
        self.hotkey = hotkey
        self.actions = Actions(self)
        self._pending = set()
        self._notices = []
        self._flush_queued = False
        self._shut = False                  # set when the window has closed for good
        self._last_head = None
        self._edited_seconds = 0.0
        self.setWindowTitle(settings.APP_NAME)
        self.setMinimumSize(MIN_SIZE)
        self.resize(self._first_size())
        self.setAcceptDrops(True)

        root = QWidget()
        root.setObjectName('root')
        self.setCentralWidget(root)
        outer = vbox(root)
        self.topbar = TopBar(self.session, self.actions)
        outer.addWidget(self.topbar)
        body = hbox()
        self.library = LibraryPanel(self.session)
        self.editor_panel = EditorPanel(self.session, self.actions)
        self.inspector = Inspector(self.session)
        body.addWidget(self.library)
        body.addWidget(self.editor_panel, 1)
        body.addWidget(self.inspector)
        outer.addLayout(body, 1)
        self._build_status_bar()
        self._build_menu()
        self._connect()
        self._restore_geometry()

        self._analysis_timer = QTimer(self)
        self._analysis_timer.setSingleShot(True)
        self._analysis_timer.setInterval(ANALYSIS_DELAY_MS)
        self._analysis_timer.timeout.connect(self._request_analysis)
        self.session.bus.listen(self._heard)
        self._refresh({'speakers', 'devices', 'takes', 'current', 'recording', 'transport', 'script', 'settings'})
        self._show_status(self.session.status_text)
        if self.hotkey is not None:
            # queued: the command runs after the system's message has been dealt with, not inside it
            self.hotkey.pressed.connect(self._hotkey_pressed, Qt.ConnectionType.QueuedConnection)
            self._apply_hotkey()
        self.timer = QTimer(self)
        self.timer.setInterval(TICK_MS)
        self.timer.timeout.connect(self._tick)
        self.timer.start()

    @staticmethod
    def _first_size():
        """The size of a window that has no saved size: 1440 by 900, or what the screen leaves, but never below the
        minimum."""
        screen = QGuiApplication.primaryScreen()
        room = screen.availableGeometry().size() if screen is not None else PREFERRED_SIZE
        return QSize(max(MIN_SIZE.width(), min(PREFERRED_SIZE.width(), room.width() - 60)),
                     max(MIN_SIZE.height(), min(PREFERRED_SIZE.height(), room.height() - 60)))

    # ---- building ----------------------------------------------------------------------------------------------------

    def _build_status_bar(self):
        bar = self.statusBar()
        bar.setSizeGripEnabled(False)
        self.status_dot = QLabel('●')
        self.status_dot.setContentsMargins(12, 0, 0, 0)
        bar.addWidget(self.status_dot)
        self.status_text = ElidedLabel()
        self.status_text.setProperty('role', 'muted')
        bar.addWidget(self.status_text, 1)
        self.status_export = label('Export 24 kHz · 16-bit · mono', 'dim')
        self.status_clean = label('', 'dim')
        self.status_drafts = label('', 'dim')
        for item in (self.status_export, self.status_clean, self.status_drafts):
            item.setStyleSheet('padding-right: 14px;')
            bar.addPermanentWidget(item)

    def _build_menu(self):
        a, menu = self.actions, self.topbar.menu
        for name in ('settings', 'import_recording', 'new_speaker', 'load_script', 'clear_script', 'open_folder'):
            menu.addAction(a[name])
        menu.addSeparator()
        menu.addAction(a.refresh_devices)
        menu.addSeparator()
        menu.addAction(a.shortcuts)
        menu.addAction(a.about)
        menu.addSeparator()
        menu.addAction(a.quit)

    def _connect(self):
        s, a = self.session, self.actions
        capturing, editor, transport, workspace = s.capturing, s.editor, s.transport, s.workspace
        a.record.triggered.connect(capturing.toggle)
        a.arm.triggered.connect(lambda: capturing.toggle_arm())
        a.play.triggered.connect(transport.toggle)
        a.stop.triggered.connect(transport.stop)
        a.loop.triggered.connect(lambda: transport.toggle_loop())
        a.ab.triggered.connect(transport.ab)
        a.escape.triggered.connect(self._escape)
        a.undo.triggered.connect(editor.undo)
        a.redo.triggered.connect(editor.redo)
        a.cut.triggered.connect(editor.cut_selection)
        a.trim.triggered.connect(editor.keep_only_selection)
        a.silence.triggered.connect(editor.silence_selection)
        a.fade_in.triggered.connect(lambda: editor.fade_selection('in'))
        a.fade_out.triggered.connect(lambda: editor.fade_selection('out'))
        a.gain.triggered.connect(self._gain)
        a.auto_analyze.triggered.connect(self.auto_analyze)
        a.auto_fix.triggered.connect(self.auto_fix)
        a.auto_trim.triggered.connect(lambda: editor.auto_trim())
        a.shorten.triggered.connect(lambda: editor.shorten_pauses())
        a.check_joins.triggered.connect(transport.check_cuts)
        a.cut_start.triggered.connect(editor.cut_from_start)
        a.cut_end.triggered.connect(editor.cut_to_end)
        a.select_all.triggered.connect(self._select_all)
        a.set_in.triggered.connect(editor.set_in)
        a.set_out.triggered.connect(editor.set_out)
        view = self.editor_panel.view
        a.zoom_in.triggered.connect(lambda: view.zoom_by(1.5, self._zoom_anchor()))
        a.zoom_out.triggered.connect(lambda: view.zoom_by(1 / 1.5, self._zoom_anchor()))
        a.fit.triggered.connect(view.fit)
        a.zoom_selection.triggered.connect(self._zoom_selection)
        a.keep.triggered.connect(self.keep)
        a.discard.triggered.connect(self.discard)
        a.rerecord.triggered.connect(self.rerecord)
        a.import_recording.triggered.connect(self.import_recording)
        a.new_speaker.triggered.connect(self.new_speaker)
        a.open_folder.triggered.connect(self.open_folder)
        a.refresh_devices.triggered.connect(capturing.refresh_devices)
        a.load_script.triggered.connect(self.load_script)
        a.clear_script.triggered.connect(s.script.clear)
        a.prev_line.triggered.connect(lambda: s.script.step(-1))
        a.next_line.triggered.connect(lambda: s.script.step(1))
        a.settings.triggered.connect(self.open_settings)
        a.shortcuts.triggered.connect(self.dialogs.shortcuts)
        a.about.triggered.connect(self.dialogs.about)
        a.quit.triggered.connect(self.close)

        self.topbar.sourceChanged.connect(capturing.set_source)
        self.topbar.deviceChanged.connect(capturing.select_device)
        self.library.speakerPicked.connect(workspace.select_speaker)
        self.library.newSpeakerRequested.connect(self.new_speaker)
        self.library.takePicked.connect(self._take_picked)
        self.library.takeContext.connect(self._take_menu)
        self.library.importRequested.connect(self.import_recording)
        self.library.folderRequested.connect(self.open_folder)
        panel = self.editor_panel
        panel.recordRequested.connect(capturing.toggle)
        panel.importRequested.connect(self.import_recording)
        panel.listen.currentChanged.connect(lambda i: transport.set_listen(('original', 'edited')[i]))
        panel.script.previousRequested.connect(lambda: s.script.step(-1))
        panel.script.nextRequested.connect(lambda: s.script.step(1))
        for timeline in (panel.waveform, panel.spectrogram):
            timeline.selectRequested.connect(editor.set_selection)
            timeline.seekRequested.connect(transport.seek)
            timeline.clickRequested.connect(self._clicked)
            timeline.contextRequested.connect(self._timeline_menu)
        inspector = self.inspector
        inspector.keepRequested.connect(self.keep)
        inspector.rerecordRequested.connect(self.rerecord)
        inspector.discardRequested.connect(self.discard)
        inspector.folderRequested.connect(self.open_folder)
        inspector.suggestRequested.connect(self.suggest_transcript)
        inspector.copyRequested.connect(self.copy_text)
        inspector.confirmRequested.connect(workspace.confirm_transcript)
        inspector.autoAnalyzeRequested.connect(self.auto_analyze)
        inspector.autoFixRequested.connect(self.auto_fix)

    # ---- hearing the session -----------------------------------------------------------------------------------------

    def _heard(self, topic, payload):
        if self._shut:
            return                                      # the window is gone; a late message has nothing to draw on
        if topic == 'status':
            self._show_status(payload)
            return
        if topic == 'notice':
            self._notices.append(payload)             # shown after the session call that raised it has returned
        else:
            self._pending.add(topic)
        if not self._flush_queued:
            self._flush_queued = True
            QTimer.singleShot(0, self.flush)

    def flush(self):
        """Redraw whatever the session reported since the last time, then show any queued messages."""
        self._flush_queued = False
        if self._shut:
            return
        topics, self._pending = self._pending, set()
        if topics:
            self._refresh(topics)
        while self._notices:
            notice = self._notices.pop(0)
            self.dialogs.notice(notice.level, notice.title, notice.text)

    def _show_status(self, text):
        self.status_text.set_full_text(text)

    def _derived(self):
        """The measurement of the current recipe if it is ready (never starts one)."""
        take = self.session.current
        if take is None:
            return None
        have = take.cache.get('derived')
        return have if have is not None and have.key == recipe_key(take.history) else None

    def _request_analysis(self):
        """Start measuring the current recipe if nobody has, and show what is known so far."""
        take = self.session.current
        if take is not None:
            self.session.measure.derived(take)
            self.session.advisor.refresh()              # only a take someone has asked Auto analyze about
        self.inspector.refresh_analysis(self._derived())

    def _refresh(self, topics):
        s = self.session
        panel = self.editor_panel
        if 'speakers' in topics:
            self.library.refresh_speakers()
        if 'devices' in topics:
            self.topbar.refresh_devices()
        if 'current' in topics:
            take = s.current
            panel.view.reset(take.seconds if take is not None else 0.0)
            if take is not None:
                s.measure.spectrogram(take)
            self._measure_edited()
            self.inspector.refresh_edits()
            self._request_analysis()
            panel.info.refresh()
            panel.listen.set_current(0 if s.transport.listen == 'original' else 1)
        if topics & {'takes', 'current', 'recording'}:
            self.library.refresh_takes()
        if 'meta' in topics and 'current' not in topics:
            self.inspector.refresh_meta(self._derived())
            self.library.refresh_takes()
        if 'edits' in topics and 'current' not in topics:
            self._measure_edited()
            self.inspector.refresh_edits()
            self.inspector.refresh_analysis(self._derived())
            self._analysis_timer.start()
            self._repaint_timelines()
        if topics & {'selection', 'playhead'}:
            panel.info.refresh()
            self._repaint_timelines()
        if 'analysis' in topics:
            derived = self._derived()
            self.inspector.refresh_analysis(derived)
            panel.spectrogram.update()
        if 'advice' in topics:
            self.inspector.refresh_advice()
        if 'recording' in topics:
            self.topbar.refresh_recording()
            if not s.capturing.recording:
                self.library.set_live(None, 0.0)
                self.topbar.meter.reset()
            self.status_dot.setStyleSheet(f'color: {PALETTE["red"] if s.capturing.recording else PALETTE["green"]};'
                                          ' padding-left: 12px;')
        if 'transport' in topics:
            self.topbar.refresh_transport()
            panel.listen.set_current(0 if s.transport.listen == 'original' else 1)
        if topics & {'script', 'current', 'recording'}:
            panel.script.refresh()
        if 'settings' in topics:
            self._refresh_status_items()
        self._update_mode()
        self._update_actions()
        self._update_clock()

    def _measure_edited(self):
        take = self.session.current
        if take is None:
            self._edited_seconds = 0.0
            return
        segments = render.segments_of(take.samples, take.rate, take.history.edits)
        self._edited_seconds = sum(b - a for a, b in segments) / take.rate

    def _repaint_timelines(self):
        panel = self.editor_panel
        panel.waveform.update()
        panel.spectrogram.update()
        panel.minimap.update()

    def _refresh_status_items(self):
        ready = self.session.drafts.available()
        self.status_clean.setText('Cleaning: FFmpeg ready' if clean.available() else 'Cleaning: FFmpeg not found')
        self.status_drafts.setText('Draft transcripts: ready' if ready else 'Draft transcripts: not set up')
        self.status_drafts.setToolTip('The speech recognizer chosen in Settings is ready.' if ready else
                                      f'Choose a speech recognizer in Settings (Ctrl+,). Settings file: '
                                      f'{self.session.settings_file}')

    def _update_mode(self):
        s = self.session
        if s.capturing.recording:
            mode, side = EditorPanel.LIVE, Inspector.RECORDING
        elif s.current is not None:
            mode, side = EditorPanel.EDIT, Inspector.REVIEW
        else:
            mode, side = EditorPanel.EMPTY, Inspector.IDLE
        self.editor_panel.set_mode(mode)
        self.inspector.set_mode(side)

    def _update_actions(self):
        s, a = self.session, self.actions
        take, editor = s.current, s.editor
        idle = not s.capturing.recording
        has_take = take is not None and idle
        has_sel = has_take and editor.sel is not None
        history = take.history if take is not None else None
        enabled = {
            'undo': has_take and history.can_undo, 'redo': has_take and history.can_redo,
            'cut': has_sel, 'trim': has_sel, 'silence': has_sel, 'fade_in': has_sel, 'fade_out': has_sel, 'gain': has_sel,
            'zoom_selection': has_sel,
            'auto_analyze': has_take, 'auto_fix': has_take,
            'auto_trim': has_take, 'shorten': has_take, 'cut_start': has_take, 'cut_end': has_take,
            'select_all': has_take, 'set_in': has_take, 'set_out': has_take, 'check_joins': has_take and bool(history.cuts),
            'play': has_take, 'stop': has_take and s.transport.active, 'loop': has_take, 'ab': has_take,
            'zoom_in': has_take, 'zoom_out': has_take, 'fit': has_take,
            'keep': has_take, 'discard': has_take, 'rerecord': has_take,
            'import_recording': idle, 'new_speaker': idle, 'settings': idle, 'arm': idle,
            'prev_line': bool(s.script.lines) and s.script.index > 0,
            'next_line': bool(s.script.lines) and s.script.index < len(s.script.lines) - 1,
            'clear_script': bool(s.script.lines),
        }
        for name, value in enabled.items():
            a[name].setEnabled(bool(value))
        a.loop.setChecked(s.transport.looping)
        a.arm.setChecked(s.capturing.armed)
        self.editor_panel.listen.setEnabled(has_take)

    def _update_clock(self):
        s = self.session
        take = s.current
        if s.capturing.recording:
            return
        if take is None:
            self.topbar.set_time(0.0, 'no take')
            return
        head = s.transport.head
        head = s.editor.playhead if head is None else head
        cuts = bool(take.history.cuts)
        sub = f'of {format_clock(self._edited_seconds)} after cuts' if cuts else f'of {format_clock(take.seconds)}'
        self.topbar.set_time(head, sub)

    # ---- the timer ---------------------------------------------------------------------------------------------------

    def _tick(self):
        s = self.session
        s.tick()
        capturing = s.capturing
        if capturing.recording:
            rows, elapsed = capturing.envelope(), capturing.elapsed
            self.editor_panel.live.set_state(rows, elapsed)
            self.library.set_live(rows, elapsed)
            sub = 'stopping...' if capturing.finishing else f'recording  ·  max {format_clock(ac.MAX_SECONDS)[:5]}'
            self.topbar.set_time(elapsed, sub)
            self.topbar.meter.set_levels(*capturing.level)
            return
        if capturing.armed:
            self.topbar.meter.set_levels(*capturing.level)
        head = s.transport.head
        if head is not None:
            self.editor_panel.view.ensure_visible(head)
        if head != self._last_head:
            self._last_head = head
            self._repaint_timelines()
            self.editor_panel.info.refresh()
            self._update_clock()

    # ---- the record key in other programs ------------------------------------------------------------------------------

    def _apply_hotkey(self):
        """Take F9 for every program, or give it back, as the settings say. When it cannot be had, say why."""
        key = self.hotkey
        wanted = settings.global_hotkey(settings.load(self.session.settings_file))
        active = key.set_enabled(wanted)
        _, _, keys, tip, _ = SPEC['record']
        also = ', also while another program is in front' if active else ''
        text = f'{tip} ({shortcut_text(keys)}{also})'
        self.actions.record.setToolTip(text)
        self.topbar.record.setToolTip(text)
        if wanted and not active:
            self.session.bus.status(f'{key.name} only works while this window is in front: {key.problem}.')

    def _hotkey_pressed(self):
        """F9 was pressed, perhaps in another program. A dialog that is open is not interrupted by a recording starting, but
        a recording can always be stopped. The taskbar button flashes, so there is a sign when this window is out of sight."""
        if self._shut or (QApplication.activeModalWidget() is not None and not self.session.capturing.recording):
            return
        self.actions.record.trigger()
        QApplication.alert(self, ALERT_MS)

    # ---- commands ----------------------------------------------------------------------------------------------------

    def _escape(self):
        if self.session.transport.active:
            self.session.transport.stop()
        else:
            self.session.editor.clear_selection()

    def _clicked(self, t):
        self.session.editor.clear_selection()
        self.session.transport.seek(t)

    def _select_all(self):
        take = self.session.current
        if take is not None:
            self.session.editor.set_selection(0.0, take.seconds)

    def _zoom_anchor(self):
        view, s = self.editor_panel.view, self.session
        head = s.transport.head if s.transport.head is not None else s.editor.playhead
        return head if view.t0 <= head <= view.t1 else (view.t0 + view.t1) / 2

    def _zoom_selection(self):
        sel = self.session.editor.sel
        if sel is not None:
            self.editor_panel.view.zoom_to(*sel)

    def auto_analyze(self):
        """Show the Check tab and have the clip analyzed there."""
        self.inspector.show_tab(Inspector.CHECK_TAB)
        self.session.advisor.analyze()

    def auto_fix(self):
        """Repair the clip as Auto analyze plans, showing the Check tab so that what was done can be read."""
        self.inspector.show_tab(Inspector.CHECK_TAB)
        self.session.advisor.fix()

    def _gain(self):
        value = self.dialogs.ask_number('Gain', 'Change the selected part by (dB):', 0.0, -60.0, 24.0, 1)
        if value:
            self.session.editor.gain_selection(value)

    def _take_picked(self, take):
        if self.session.capturing.recording:
            self.session.bus.status('Stop the recording before opening another take.')
            return
        self.session.workspace.open_take(take)

    def keep(self):
        s = self.session
        take = s.current
        if take is None:
            return
        if take.needs_transcript_review:
            if not self.dialogs.confirm('Check the transcript', 'You cut or silenced part of this clip. Does the '
                                        'transcript still match the words that are kept?', yes='It matches', no='Go back'):
                self.inspector.show_tab(0)
                return
            s.workspace.confirm_transcript()
        try:
            s.keep()
        except KeepBlocked as e:
            self.dialogs.notice('warning', 'Cannot keep yet', '\n'.join(f'- {p}' for p in e.problems))
        except CleanError as e:
            self.dialogs.notice('error', 'Could not clean the audio', str(e))
        except OSError as e:
            self.dialogs.notice('error', 'Could not write the files', str(e))

    def _discard_current(self, title, text):
        """Ask, then move the take's files to the Recycle Bin (or delete them if the person insists). True if it is gone."""
        if not self.dialogs.confirm(title, text, yes='Move to Recycle Bin'):
            return False
        workspace = self.session.workspace
        try:
            return workspace.discard()
        except TrashUnavailable as e:
            if self.dialogs.confirm('The Recycle Bin is not available',
                                    f'{e}\n\nDelete the files for good instead? This cannot be undone.',
                                    yes='Delete permanently', danger=True):
                return workspace.discard(permanent=True)
            return False

    def discard(self):
        if self.session.current is not None:
            self._discard_current('Discard this take?', 'The take moves to the Recycle Bin with its original recording, '
                                                        'its export and its sidecar. You can restore it from there.')

    def rerecord(self):
        if self.session.current is not None and self._discard_current(
                'Re-record?', 'This take moves to the Recycle Bin and a new recording starts straight away.'):
            self.session.capturing.start()

    def import_recording(self):
        path = self.dialogs.choose_file('Import a recording', self.session.lib.root)
        if path:
            self.session.workspace.import_file(path)

    def new_speaker(self):
        name = self.dialogs.ask_text('New speaker', 'Name of the voice you are recording:')
        if name:
            try:
                self.session.workspace.add_speaker(name)
            except ValueError as e:
                self.dialogs.notice('warning', 'New speaker', str(e))

    def open_folder(self):
        s = self.session
        folder = s.lib.folder(s.workspace.speaker) if s.workspace.speaker else s.lib.root
        try:
            folder.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            self.dialogs.notice('error', 'Could not open the folder', str(e))
            return
        self.dialogs.open_path(folder)

    def load_script(self):
        path = self.dialogs.choose_file('Load a script', self.session.lib.root, 'Text files (*.txt);;All files (*)')
        if path:
            self.session.script.load_file(path)

    def suggest_transcript(self):
        take = self.session.current
        if take is None:
            return
        typed = take.meta['transcript'].strip() and not self.session.drafts.machine_text(take)
        if typed and not self.dialogs.confirm(
                'Replace the transcript?', 'The draft will replace what is typed now, unless you change the text again '
                'while it is being made.', yes='Make a draft'):
            return
        self.session.drafts.request(take, replace=True)

    def copy_text(self, what):
        take = self.session.current
        if take is None:
            return
        text = take.meta['transcript'].strip() if what == 'transcript' else str(take.export_path or '')
        if text:
            QApplication.clipboard().setText(text)
            self.session.bus.status('Copied the transcript.' if what == 'transcript' else 'Copied the file path.')

    def open_settings(self):
        s = self.session
        current = settings.load(s.settings_file)
        current.setdefault('root', str(s.lib.root))
        current['tail_seconds'] = s.capturing.tail_seconds
        answer = self.dialogs.settings(current)
        if answer is None:
            return
        changes = {key: answer[key] for key in ('tail_seconds', 'ffmpeg', 'asr_python', 'asr_model')}
        changes['asr_language'] = answer.get('asr_language')
        changes['global_hotkey'] = None if answer.get('global_hotkey', True) else False      # on is the default: no entry
        settings.update(changes, s.settings_file)
        clean.set_ffmpeg_path(answer['ffmpeg'])
        s.capturing.set_tail(answer['tail_seconds'])
        if answer['root'] and Path(answer['root']) != s.lib.root:
            s.workspace.set_root(answer['root'])
        s.bus.notify('settings')
        if self.hotkey is not None:
            self._apply_hotkey()

    # ---- menus -------------------------------------------------------------------------------------------------------

    def build_timeline_menu(self, t):
        """The right-click menu of the waveform at time `t`."""
        s, a = self.session, self.actions
        take, editor = s.current, s.editor
        menu = QMenu(self)
        for name in ('cut', 'trim', 'silence', 'fade_in', 'fade_out', 'gain'):
            menu.addAction(a[name])
        menu.addSeparator()
        for name in ('cut_start', 'cut_end', 'select_all'):
            menu.addAction(a[name])
        hits = [(i, e) for i, e in enumerate(take.history.edits) if e['start_seconds'] <= t <= e['end_seconds']]
        if hits:
            menu.addSeparator()
            for i, edit in hits:
                action = menu.addAction(f'Remove: {describe(edit)}')
                action.triggered.connect(lambda checked=False, index=i: editor.remove_edit(index))
        menu.addSeparator()
        menu.addAction(a.zoom_selection)
        menu.addAction(a.fit)
        return menu

    def _timeline_menu(self, pos, t):
        editor = self.session.editor
        if editor.sel is None or not editor.sel[0] <= t <= editor.sel[1]:
            self.session.transport.seek(t)
        self.dialogs.popup(self.build_timeline_menu(t), pos)

    def build_take_menu(self, take):
        menu = QMenu(self)
        menu.addAction('Open', lambda: self._take_picked(take))
        copy = menu.addAction('Copy file path', lambda: QApplication.clipboard().setText(str(take.export_path or take.master_path)))
        copy.setEnabled((take.export_path or take.master_path) is not None)
        menu.addSeparator()
        menu.addAction('Discard...', lambda: (self._take_picked(take), self.discard()))
        return menu

    def _take_menu(self, take, pos):
        if not self.session.capturing.recording:
            self.dialogs.popup(self.build_take_menu(take), pos)

    # ---- dropping files ----------------------------------------------------------------------------------------------

    @staticmethod
    def _dropped_audio(mime):
        paths = [Path(url.toLocalFile()) for url in mime.urls() if url.isLocalFile()]
        return [p for p in paths if p.suffix.lower() in AUDIO_SUFFIXES]

    def dragEnterEvent(self, event):
        if self._dropped_audio(event.mimeData()) and not self.session.capturing.recording:
            event.acceptProposedAction()

    def dropEvent(self, event):
        for path in self._dropped_audio(event.mimeData()):
            self.session.workspace.import_file(path)
        event.acceptProposedAction()

    # ---- closing -----------------------------------------------------------------------------------------------------

    def _restore_geometry(self):
        saved = settings.load(self.session.settings_file).get('window')
        if isinstance(saved, str):
            try:
                self.restoreGeometry(QByteArray(base64.b64decode(saved)))
            except (ValueError, TypeError):
                pass

    def _save_geometry(self):
        try:
            text = base64.b64encode(bytes(self.saveGeometry())).decode('ascii')
            settings.update({'window': text}, self.session.settings_file)
        except OSError:
            pass

    def closeEvent(self, event):
        failures = self.session.close()
        if failures:
            self.dialogs.notice('error', 'Could not save every recording',
                                '\n'.join(failures) + '\n\nThe window stays open so the audio is not lost. Free up space or '
                                'fix the folder, then close again.')
            event.ignore()
            return
        self.timer.stop()
        if self.hotkey is not None:
            self.hotkey.set_enabled(False)
        self._save_geometry()
        self._shut = True
        event.accept()
        self.closed.emit()
