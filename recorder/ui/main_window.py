"""The main window: a thin view over a `Session`.

Nothing here decides anything. A click or a key calls a session method; the session reports what changed on its bus;
the window redraws the parts that heard about it. A timer calls `session.tick()` about twenty times a second, which
is also when the meter, the clock, the live waveform and the playhead are updated.
"""
import base64
from pathlib import Path

from PySide6.QtCore import QByteArray, QSize, QStandardPaths, Qt, QTimer, Signal
from PySide6.QtGui import QAccessible, QAccessibleAnnouncementEvent, QGuiApplication
from PySide6.QtWidgets import QApplication, QLabel, QMainWindow, QMenu, QWidget

from .. import asr_setup, audio_import, dataset, launch, manifest, share, transcript
from .. import clean, settings
from ..audio_io import list_outputs
from ..clean import CleanError
from ..core import render
from ..core.edits import describe
from ..i18n import tr
from ..session import Session
from ..session.measure import take_key
from ..store import KeepBlocked, Relocation, TrashUnavailable, clean_label
from .actions import SPEC, Actions
from .badge import RecordingBadge
from .dialogs import Dialogs
from .editor_panel import EditorPanel
from .inspector import Inspector
from .library import LibraryPanel
from .palette import PALETTE
from .topbar import TopBar
from .translate import translate_tree
from .widgets import ElidedLabel, button, format_clock, hbox, label, vbox

MIN_SIZE = QSize(1100, 600)        # 700 did not fit a 1080p screen at 150%, once the title bar and the taskbar were counted
PREFERRED_SIZE = QSize(1440, 900)
AUDIO_SUFFIXES = set(audio_import.SUFFIXES)
TICK_MS = 50
ANALYSIS_DELAY_MS = 200            # how long the measurements wait after the last edit, so a dragged slider measures once
ALERT_MS = 2000                    # how long the taskbar button flashes when the record key was pressed in another program
ANNOUNCE_AFTER_MS = 400            # a burst of status messages is read out as its last one, not stammered through


class MainWindow(QMainWindow):
    closed = Signal()
    unexpected = Signal(str)                # an error nothing caught, from any thread (recorder/ui/__init__.py sends these)

    def __init__(self, session=None, dialogs=None, settings_file=None, hotkey=None):
        """`hotkey` is the key that reaches this window from other programs (recorder/hotkey.py); without one, the record
        key only works while the window is in front. `run()` supplies it, so no test or tool takes a key from the machine it
        runs on."""
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
        self._errors_shown = set()
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
        self.badge = RecordingBadge(self)
        self.badge.stopRequested.connect(self.session.capturing.stop)
        self._apply_record_key()
        translate_tree(self)

        self._analysis_timer = QTimer(self)
        self._analysis_timer.setSingleShot(True)
        self._analysis_timer.setInterval(ANALYSIS_DELAY_MS)
        self._analysis_timer.timeout.connect(self._request_analysis)
        self.session.bus.listen(self._heard)
        self.unexpected.connect(self._show_unexpected, Qt.ConnectionType.QueuedConnection)
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
        bar.setAccessibleName('Status')
        self.status_dot = QLabel('●')
        self.status_dot.setAccessibleName('Ready')
        self.status_dot.setContentsMargins(12, 0, 0, 0)
        bar.addWidget(self.status_dot)
        self.status_text = ElidedLabel()
        self.status_text.setProperty('role', 'muted')
        bar.addWidget(self.status_text, 1)
        self._announcer = QTimer(self)
        self._announcer.setSingleShot(True)
        self._announcer.setInterval(ANNOUNCE_AFTER_MS)
        self._announcer.timeout.connect(self._announce)
        self.status_stop = button('Stop', 'link', None, 'Stop after the take being worked on. What is done stays done')
        self.status_stop.clicked.connect(self.session.batch.cancel)
        self.status_stop.hide()
        bar.addPermanentWidget(self.status_stop)
        self.status_export = label('', 'dim')
        self.status_clean = label('', 'dim')
        self.status_drafts = label('', 'dim')
        for item in (self.status_export, self.status_clean, self.status_drafts):
            item.setStyleSheet('padding-right: 14px;')
            bar.addPermanentWidget(item)

    def _build_menu(self):
        a, menu = self.actions, self.topbar.menu
        for group in (('settings', 'import_recording', 'new_speaker', 'rename_speaker', 'speaker_details', 'delete_speaker'),
                      ('move_take', 'save_copy', 'export_manifest', 'export_dataset', 'split_take'),
                      ('keep_selected', 'discard_selected', 'move_selected'),
                      ('load_script', 'clear_script', 'open_folder')):
            for name in group:
                menu.addAction(a[name])
            menu.addSeparator()
        menu.addAction(a.refresh_devices)
        menu.addSeparator()
        menu.addAction(a.shortcuts)
        menu.addAction(a.diagnostics)
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
        a.rename.triggered.connect(self.rename_take)
        a.import_recording.triggered.connect(self.import_recording)
        a.new_speaker.triggered.connect(self.new_speaker)
        a.rename_speaker.triggered.connect(self.rename_speaker)
        a.speaker_details.triggered.connect(self.speaker_details)
        a.delete_speaker.triggered.connect(self.delete_speaker)
        a.export_dataset.triggered.connect(self.export_dataset)
        a.move_take.triggered.connect(self.move_take)
        a.save_copy.triggered.connect(self.save_copy)
        a.export_manifest.triggered.connect(self.export_manifest)
        a.open_folder.triggered.connect(self.open_folder)
        a.refresh_devices.triggered.connect(capturing.refresh_devices)
        a.load_script.triggered.connect(self.load_script)
        a.clear_script.triggered.connect(s.script.clear)
        a.prev_line.triggered.connect(lambda: s.script.step(-1))
        a.next_line.triggered.connect(lambda: s.script.step(1))
        a.next_open.triggered.connect(self.inspector.script._next)
        a.next_take.triggered.connect(lambda: self.step_take(1))
        a.prev_take.triggered.connect(lambda: self.step_take(-1))
        a.split_take.triggered.connect(self.split_take)
        a.keep_selected.triggered.connect(lambda: self.keep_selected())
        a.discard_selected.triggered.connect(lambda: self.discard_selected())
        a.move_selected.triggered.connect(lambda: self.move_selected())
        a.settings.triggered.connect(self.open_settings)
        a.shortcuts.triggered.connect(self.dialogs.shortcuts)
        a.diagnostics.triggered.connect(self.export_diagnostics)
        a.about.triggered.connect(self.dialogs.about)
        a.quit.triggered.connect(self.close)

        self.topbar.sourceChanged.connect(capturing.set_source)
        self.topbar.deviceChanged.connect(capturing.select_device)
        self.topbar.channelChanged.connect(capturing.select_channel)
        self.library.speakerPicked.connect(workspace.select_speaker)
        self.library.newSpeakerRequested.connect(self.new_speaker)
        self.library.speakerContext.connect(self._speaker_menu)
        self.library.purposePicked.connect(workspace.set_purpose)
        self.library.takePicked.connect(self._take_picked)
        self.library.takeContext.connect(self._take_menu)
        self.library.selectionContext.connect(self._selection_menu)
        self.library.selectionChanged.connect(self._update_actions)
        self.library.discardRequested.connect(lambda: self.discard_selected())
        self.library.importRequested.connect(self.import_recording)
        self.library.folderRequested.connect(self.open_folder)
        panel = self.editor_panel
        panel.recordRequested.connect(capturing.toggle)
        panel.importRequested.connect(self.import_recording)
        panel.listen.currentChanged.connect(lambda i: transport.set_listen(('original', 'edited')[i]))
        panel.script.previousRequested.connect(lambda: s.script.step(-1))
        panel.script.nextRequested.connect(lambda: s.script.step(1))
        panel.words.wordsPicked.connect(editor.select_words)
        panel.words.wordRestored.connect(editor.restore_word)
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
        inspector.loadScriptRequested.connect(self.load_script)

    # ---- hearing the session -----------------------------------------------------------------------------------------

    def _heard(self, topic, payload):
        if self._shut:
            return                                      # the window is gone; a late message has nothing to draw on
        if topic == 'status':
            self._show_status(payload)
            return
        if topic == 'thumb':
            self.library.take_changed(payload)
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
        text = tr(text)
        self.status_text.set_full_text(text)
        self.status_text.setAccessibleName(text)            # what a screen reader says when asked about the status line
        self._announcer.start()

    def _announce(self):
        """Have a screen reader say the status line. It says nothing when a label changes by itself, and after Keep or Record
        this line is all the answer there is."""
        QAccessible.updateAccessibility(QAccessibleAnnouncementEvent(self.status_text, self.status_text.accessibleName()))

    def _show_unexpected(self, text):
        """Something failed that nothing here was ready for. Say so instead of leaving the person to wonder why nothing
        happened, but once for each message: a fault in something that repeats would otherwise open a dialog twenty times a
        second."""
        self._show_status(f'Something went wrong: {text}')
        if text not in self._errors_shown:
            self._errors_shown.add(text)
            self.dialogs.notice('error', 'Something went wrong', f'{text}\n\nThe last action may not have finished. Your '
                                f'recordings are not affected. The details are in {launch.log_path()}')

    def _derived(self):
        """The measurement of the current recipe if it is ready (never starts one)."""
        take = self.session.current
        if take is None:
            return None
        have = take.cache.get('derived')
        return have if have is not None and have.key == take_key(take) else None

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
        elif 'recording' in topics:
            self.library.refresh_enabled()
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
            if s.current is not None and s.current.kept:
                self.library.refresh_takes()            # its card says whether it still matches the kept clip
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
            self.status_dot.setAccessibleName(tr('Recording' if s.capturing.recording else 'Ready'))
        if 'transport' in topics:
            self.topbar.refresh_transport()
            panel.listen.set_current(0 if s.transport.listen == 'original' else 1)
        if topics & {'script', 'current', 'recording', 'takes'}:
            panel.script.refresh()
        if topics & {'script', 'takes'}:
            self.inspector.refresh_script()
        if topics & {'current', 'edits', 'selection', 'meta', 'recording'}:
            panel.words.refresh()
        if 'batch' in topics:
            self.status_stop.setVisible(s.batch.running)
        if 'settings' in topics:
            self._refresh_status_items()
        if topics & {'settings', 'current', 'meta'}:
            self._refresh_export_item()
            self.library.refresh_purpose()
        if topics & {'settings', 'speakers', 'current'}:
            self.library.refresh_release()
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

    def _refresh_export_item(self):
        """What Keep writes for the open take, or for a new one when none is open."""
        take = self.session.current
        rules = take.purpose if take is not None else self.session.workspace.purpose
        rate = take.export_rate if take is not None else rules.export_rate
        self.status_export.setText(f'{tr(rules.name)}: {rate / 1000:g} kHz · {rules.bit_depth}-bit · mono')

    def _refresh_status_items(self):
        ready = self.session.drafts.available()
        self.status_clean.setText(tr('Cleaning: FFmpeg ready' if clean.available() else 'Cleaning: FFmpeg not found'))
        self.status_drafts.setText(tr('Draft transcripts: ready' if ready else 'Draft transcripts: not set up'))
        self.status_drafts.setToolTip(tr('The speech recognizer chosen in Settings is ready.') if ready else
                                      f'{tr("Choose a speech recognizer in Settings (Ctrl+,). Settings file:")} '
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
        idle = not s.capturing.recording and not s.batch.running
        has_take = take is not None and idle
        chosen = self.library.selected_takes()
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
            'keep': has_take, 'discard': has_take, 'rerecord': has_take, 'rename': has_take,
            'import_recording': idle, 'new_speaker': idle, 'settings': idle, 'arm': idle, 'record': not s.batch.running,
            'split_take': has_take, 'next_take': idle and bool(s.workspace.takes), 'prev_take': idle and bool(s.workspace.takes),
            'keep_selected': idle and len(chosen) > 1, 'discard_selected': idle and len(chosen) > 1,
            'move_selected': idle and len(chosen) > 1 and len(s.workspace.speakers) > 1,
            'rename_speaker': idle and s.workspace.speaker is not None,
            'speaker_details': idle and s.workspace.speaker is not None,
            'delete_speaker': idle and s.workspace.speaker is not None,
            'export_dataset': idle and s.workspace.speaker is not None,
            'move_take': has_take and not take.unsaved and len(s.workspace.speakers) > 1,
            'save_copy': has_take and take.kept,
            'export_manifest': idle and s.workspace.speaker is not None,
            'prev_line': bool(s.script.lines) and s.script.index > 0,
            'next_line': bool(s.script.lines) and s.script.index < len(s.script.lines) - 1,
            'next_open': bool(s.script.lines),
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
            self.topbar.set_time(0.0, tr('no take'))
            return
        head = s.transport.head
        head = s.editor.playhead if head is None else head
        cuts = bool(take.history.cuts)
        sub = tr('of {time} after cuts').format(time=format_clock(self._edited_seconds)) if cuts else \
            tr('of {time}').format(time=format_clock(take.seconds))
        self.topbar.set_time(head, sub)

    # ---- the timer ---------------------------------------------------------------------------------------------------

    def _tick(self):
        s = self.session
        s.tick()
        capturing = s.capturing
        if capturing.recording:
            rows, elapsed, rules = capturing.envelope(), capturing.elapsed, s.workspace.purpose
            self.editor_panel.live.set_state(rows, elapsed, rules)
            self.library.set_live(rows, elapsed)
            sub = tr('stopping...') if capturing.finishing else \
                tr('recording  ·  max {time}').format(time=format_clock(rules.record_limit)[:5])
            self.topbar.set_time(elapsed, sub)
            self.topbar.meter.set_levels(*capturing.level)
            self._refresh_badge()
            return
        if self.badge.isVisible():
            self.badge.hide()
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

    # ---- the record key ----------------------------------------------------------------------------------------------------

    def _record_key(self):
        return settings.record_key(settings.load(self.session.settings_file))

    def _refresh_badge(self):
        """A recording that runs while this window is not the one in front has a sign of its own on top of everything."""
        if self.isActiveWindow():
            self.badge.hide()
        else:
            self.badge.show_for(self.session.capturing.elapsed, self._record_key())

    def _apply_record_key(self):
        """Put the record key from the settings on the Record command and everything that names it."""
        key = self._record_key()
        self.actions.set_record_key(key)
        self.topbar.record_key = key
        self.topbar.record.setToolTip(self.actions.record.toolTip())
        self.topbar.refresh_recording()
        self.editor_panel.empty.record.setText(f'{tr("Record")}  ({key})')

    def _apply_hotkey(self):
        """Take the record key for every program, or give it back, as the settings say. When it cannot be had, say why."""
        key = self.hotkey
        wanted = settings.global_hotkey(settings.load(self.session.settings_file))
        if key.name != self._record_key():
            key.set_enabled(False)
            key.set_key(self._record_key())
        active = key.set_enabled(wanted)
        _, _, _, tip, _ = SPEC['record']
        also = ', also while another program is in front' if active else ''
        text = f'{tip} ({key.name}{also})'
        self.actions.record.setToolTip(text)
        self.topbar.record.setToolTip(text)
        if wanted and not active:
            self.session.bus.status(f'{key.name} only works while this window is in front: {key.problem}.')

    def _hotkey_pressed(self):
        """The record key was pressed, perhaps in another program. A dialog that is open is not interrupted by a recording
        starting, but a recording can always be stopped. The taskbar button flashes, and the sign on top shows the recording
        while this window is out of sight."""
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
        """Ctrl+A: every take when the list has the focus, otherwise the whole clip."""
        take = self.session.current
        if self.library.list.hasFocus():
            self.library.list.selectAll()
        elif take is not None:
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
        if self.session.batch.running:
            self.session.bus.status('Wait for this to finish, or press Stop, before opening another take.')
            return
        self.session.workspace.open_take(take)

    def step_take(self, delta):
        """Open the take `delta` rows down the list, as it is filtered now."""
        takes = [item for item in self.library.model.items if item is not None]
        current = self.session.current
        if not takes:
            return
        place = takes.index(current) + delta if current in takes else 0
        if 0 <= place < len(takes):
            self._take_picked(takes[place])

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

    def keep_selected(self, takes=None):
        """Keep the selected takes, once the person has made the statement for all of them in the dialog."""
        s = self.session
        takes = self.library.selected_takes() if takes is None else takes
        wanted = [take for take in takes if not take.kept or take.changed]
        if not wanted:
            self.dialogs.notice('info', 'Keep several takes', 'Every take selected is kept already, with nothing to update.')
            return
        answer = self.dialogs.keep_many(s.workspace.name_of(s.workspace.speaker), wanted,
                                        s.lib.speaker_defaults(s.workspace.speaker))
        if answer is not None:
            s.batch.keep(wanted, *answer)

    def discard_selected(self, takes=None):
        """Delete pressed in the list, or the command: one take as Discard does, several after one question."""
        s = self.session
        takes = self.library.selected_takes() if takes is None else takes
        if s.capturing.recording or s.batch.running or not takes:
            return
        if len(takes) == 1:
            s.workspace.open_take(takes[0])
            self.discard()
            return
        kept = sum(1 for take in takes if take.kept)
        also = f' {kept} of them are kept clips, and their WAV files and sidecars go too.' if kept else ''
        if self.dialogs.confirm(f'Discard {len(takes)} takes?', f'They move to the Recycle Bin, with their original '
                                f'recordings.{also} You can restore them from there.', yes='Move to Recycle Bin'):
            s.batch.discard(takes)

    def move_selected(self, takes=None, slug=None):
        """File the selected takes under another speaker, asking which unless `slug` says."""
        s = self.session
        takes = self.library.selected_takes() if takes is None else takes
        if not takes or s.capturing.recording or s.batch.running:
            return
        if slug is None:
            others = self._other_speakers(takes[0])
            index = self.dialogs.choose_item('Move takes', f'File these {len(takes)} takes under:',
                                             [name for _, name in others]) if others else None
            if index is None:
                return
            slug = others[index][0]
        kept = sum(1 for take in takes if take.kept)
        if kept and not self.dialogs.confirm(
                'Move these kept clips?', f'{kept} kept clip{"s" if kept != 1 else ""} will be filed under '
                f'{s.workspace.name_of(slug)}. Permission, origin and allowed use go with each one, and its saved '
                'description will say where it was moved from.', yes='Move'):
            return
        s.batch.move(takes, slug)

    def split_take(self):
        """Make a new take of each part of the open take, after saying how many there would be."""
        s = self.session
        take = s.current
        if take is None or s.capturing.recording:
            return
        ranges = s.workspace.split_ranges(take)
        if len(ranges) < 2:
            self.dialogs.notice('info', 'Split at pauses', f'There is nowhere to split this take: it has no pause of at least '
                                f'a third of a second after its first {take.purpose.best_from:g} seconds.')
            return
        lengths = ', '.join(f'{(end - first) / take.rate:.0f}' for first, end in ranges[:8])
        if self.dialogs.confirm('Split this take?', f'It will become {len(ranges)} new takes, cut in the middle of its pauses '
                                f'({lengths}{", ..." if len(ranges) > 8 else ""} seconds). This take stays as it is, and the '
                                'new takes start from the original recording, without its edits.', yes=f'Make {len(ranges)} takes'):
            try:
                s.workspace.split_take(take)
            except OSError as e:
                self.dialogs.notice('error', 'Could not split the take', str(e))

    def rerecord(self):
        if self.session.current is not None and self._discard_current(
                'Re-record?', 'This take moves to the Recycle Bin and a new recording starts straight away.'):
            self.session.capturing.start()

    def revert_take(self, take):
        """Drop what has been changed on a kept clip since Keep, after asking."""
        if not self.dialogs.confirm('Go back to the kept clip?', 'The changes made since this clip was kept will be '
                                    'dropped. The kept clip itself has not changed.', yes='Drop the changes', danger=True):
            return
        try:
            self.session.workspace.revert(take)
        except (OSError, ValueError) as e:
            self.dialogs.notice('error', 'Could not go back to the kept clip', str(e))

    def rename_take(self):
        """Put the cursor in the Label field of the take in front, so it can be given a name."""
        if self.session.current is not None and not self.session.capturing.recording:
            self.inspector.focus_label()

    def import_recording(self):
        self.import_files(self.dialogs.choose_files('Import recordings', self.session.lib.root))

    def import_files(self, paths):
        """Copy recordings in as takes, the last one in front. One that cannot be read is said in a notice, once the
        rest are in. FFmpeg can take a few seconds over a long M4A, so the cursor shows that the window is busy."""
        paths = list(paths)
        if not paths:
            return
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            imported = sum(1 for path in paths if self.session.workspace.import_file(path) is not None)
        finally:
            QApplication.restoreOverrideCursor()
        if imported > 1:
            self.session.bus.status(f'Imported {imported} recordings. Edit each one, then Keep.')

    def new_speaker(self):
        name = self.dialogs.ask_text('New speaker', 'Name of the voice you are recording:')
        if name:
            try:
                self.session.workspace.add_speaker(name)
            except ValueError as e:
                self.dialogs.notice('warning', 'New speaker', str(e))

    def rename_speaker(self):
        workspace = self.session.workspace
        slug = workspace.speaker
        if not slug:
            return
        old = workspace.name_of(slug)
        name = self.dialogs.ask_text('Rename speaker', 'The name to show this voice under:', old)
        if name and name != old:
            try:
                workspace.rename_speaker(slug, name)
            except ValueError as e:
                self.dialogs.notice('warning', 'Rename speaker', str(e))

    def speaker_details(self):
        """The signed release of the speaker in front."""
        s = self.session
        slug = s.workspace.speaker
        if not slug:
            return
        record = s.lib.release(slug)
        answer = self.dialogs.speaker_details(s.workspace.name_of(slug), record, s.lib.release_path(slug) is not None)
        if answer is None:
            return
        try:
            s.lib.set_release(slug, **answer)
        except ValueError as e:
            self.dialogs.notice('warning', 'Speaker details', str(e))
        except OSError as e:
            self.dialogs.notice('error', 'Could not save the release', str(e))
        s.bus.notify('settings')
        s.bus.notify('meta')

    def delete_speaker(self):
        """Delete the speaker in front, with everything in their folder, after saying how much that is."""
        s = self.session
        workspace = s.workspace
        slug = workspace.speaker
        if not slug:
            return
        kept, unkept = s.lib.speaker_contents(slug)
        name = workspace.name_of(slug)
        if not self.dialogs.confirm(f'Delete {name}?', f'Their folder moves to the Recycle Bin: {kept} kept '
                                    f'clip{"" if kept == 1 else "s"}, {unkept} other take{"" if unkept == 1 else "s"}, the '
                                    'original recordings, and their release. You can restore it from there, but '
                                    f'{name} leaves the list of speakers.', yes='Move to Recycle Bin', danger=True):
            return
        try:
            workspace.delete_speaker(slug)
        except TrashUnavailable as e:
            if self.dialogs.confirm('The Recycle Bin is not available', f'{e}\n\nDelete the folder for good instead? This '
                                    'cannot be undone.', yes='Delete permanently', danger=True):
                workspace.delete_speaker(slug, permanent=True)
        except ValueError as e:
            self.dialogs.notice('warning', 'Delete speaker', str(e))

    def _other_speakers(self, take):
        return [(slug, name) for slug, name in self.session.workspace.speakers if slug != take.speaker]

    def move_take(self):
        """Ask which speaker, then file the open take under them."""
        take = self.session.current
        if take is None:
            return
        others = self._other_speakers(take)
        if not others:
            self.dialogs.notice('info', 'Move take', 'There is no other speaker yet. Add one with New speaker, then move '
                                                      'the take.')
            return
        index = self.dialogs.choose_item('Move take', 'File this take under:', [name for _, name in others])
        if index is not None:
            self._move(take, others[index][0])

    def _move(self, take, slug):
        """File `take` under the speaker `slug`. A kept clip asks first: it is a statement about whose voice it is."""
        workspace = self.session.workspace
        if self.session.capturing.recording:
            self.session.bus.status('Stop the recording before moving a take.')
            return
        if take.kept and not self.dialogs.confirm(
                'Move this kept clip?',
                f'The clip will be filed under {workspace.name_of(slug)}. Its permission, origin and allowed use go with '
                f'it, and its saved description will say it was moved from {workspace.name_of(take.speaker)}.',
                yes='Move clip'):
            return
        try:
            workspace.move_take(slug, take)
        except (ValueError, OSError) as e:
            self.dialogs.notice('error', 'Could not move the take', str(e))

    @staticmethod
    def _with_ending(path, ending, known):
        """The chosen file name with the ending the format needs: added if it has none, put in place of another ending of
        a format on offer, and left alone otherwise."""
        path = Path(path)
        if path.suffix.lower() == ending:
            return path
        if path.suffix.lower() in known:
            return path.with_suffix(ending)
        return path.with_name(path.name + ending)

    def save_copy(self):
        """Save a copy of the open kept clip as WAV, FLAC, MP3 or Ogg, at a sample rate chosen here."""
        s = self.session
        take = s.current
        if take is None or not take.kept or s.capturing.recording:
            return
        if not Path(take.export_path).is_file():
            self.dialogs.notice('error', 'Save a copy', f'The kept clip {Path(take.export_path).name} is not in its folder '
                                                        'any more.')
            return
        name = clean_label(take.meta.get('label')) or ' '.join(str(take.meta.get('transcript') or '').split())[:60] \
            or Path(take.export_path).stem
        choice = self.dialogs.copy_options(name, take.export_rate)
        if choice is None:
            return
        kind, rate = choice
        text, ending = share.FORMATS[kind][:2]
        known = {entry[1] for entry in share.FORMATS.values()}
        path = self.dialogs.choose_save_file('Save a copy', share.filename_for(name, Path(take.export_path).stem) + ending,
                                             f'{text} (*{ending})')
        if not path:
            return
        path = self._with_ending(path, ending, known)
        try:
            Path(path).resolve().relative_to(s.lib.root.resolve())
        except (ValueError, OSError):
            pass
        else:
            self.dialogs.notice('warning', 'Save a copy', 'Choose a place outside the output folder, so that the copy is not '
                                                          'mistaken for a kept clip.')
            return
        try:
            share.save_copy(take.export_path, path, kind, rate)
        except OSError as e:                        # share.CopyError is one
            self.dialogs.notice('error', 'Could not save the copy', str(e))
            return
        s.bus.status(f'Saved a copy ({share.rate_text(rate)}) to {path}')

    def export_manifest(self):
        """List the kept clips of this speaker, or of everyone, in a CSV or JSON Lines file."""
        s = self.session
        workspace = s.workspace
        slug = workspace.speaker
        if slug is None or s.capturing.recording:
            return
        choice = self.dialogs.manifest_options(workspace.name_of(slug))
        if choice is None:
            return
        scope, kind = choice
        text, ending = manifest.FORMATS[kind]
        known = {entry[1] for entry in manifest.FORMATS.values()}
        folder = s.lib.folder(slug) if scope == 'speaker' and s.lib.folder(slug).is_dir() else s.lib.root
        name = f'{slug}-manifest' if scope == 'speaker' else 'manifest'
        path = self.dialogs.choose_save_file('Export manifest', folder / (name + ending), f'{text} (*{ending})')
        if not path:
            return
        path = self._with_ending(path, ending, known)
        error = None
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            rows, left_out = manifest.collect(s.lib, [slug] if scope == 'speaker' else None, base=path.parent)
            if rows:
                manifest.write(path, rows, kind)
        except (OSError, ValueError) as e:
            error = e
        finally:
            QApplication.restoreOverrideCursor()
        if error is not None:                       # said once the busy cursor is gone, not under it
            self.dialogs.notice('error', 'Could not export the manifest', str(error))
            return
        detail = '\n'.join(f'- {file}: {reason}' for file, reason in left_out)
        if not rows:
            self.dialogs.notice('info', 'Export manifest', 'There are no kept clips to list yet.' +
                                (f'\n\nLeft out:\n{detail}' if detail else ''))
            return
        s.bus.status(f'Listed {len(rows)} clip{"s" if len(rows) != 1 else ""} in {path}' +
                     (f'. {len(left_out)} left out.' if left_out else ''))
        if left_out:
            self.dialogs.notice('warning', 'Some clips were left out',
                                f'{len(rows)} clip{"s are" if len(rows) != 1 else " is"} in the manifest. These are not:'
                                f'\n\n{detail}')

    def export_dataset(self):
        """Gather the kept clips in a new folder, a few at a time, so the window stays alive and it can be stopped."""
        s = self.session
        workspace = s.workspace
        slug = workspace.speaker
        if slug is None or s.capturing.recording or s.batch.running:
            return
        choice = self.dialogs.dataset_options(workspace.name_of(slug))
        if choice is None:
            return
        scope, layout, rate, originals = choice
        folder = self.dialogs.choose_folder('Choose an empty folder for the dataset', '')
        if not folder:
            return
        try:
            export = dataset.Export(s.lib, folder, layout, rate, originals)
        except (ValueError, OSError) as e:
            self.dialogs.notice('warning', 'Export dataset', str(e))
            return
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            rows, left_out = manifest.collect(s.lib, [slug] if scope == 'speaker' else None)
        finally:
            QApplication.restoreOverrideCursor()
        if not rows:
            self.dialogs.notice('info', 'Export dataset', 'There are no kept clips to export yet.')
            return
        s.batch.export_dataset(export, rows)
        s.batch.left_out.extend(left_out)

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

    def export_diagnostics(self):
        """Save a zip for a bug report, where the person chooses: what is installed, the settings and the end of the log,
        with their folders, user name and speakers' names replaced (diagnostics.py)."""
        from datetime import date

        from .. import diagnostics

        desktop = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DesktopLocation) or str(Path.home())
        start = Path(desktop) / f'voice-slate-diagnostics-{date.today()}.zip'
        path = self.dialogs.choose_save_file('Export diagnostics', start, 'Zip files (*.zip);;All files (*)')
        if not path:
            return
        try:
            made = diagnostics.collect(self._with_ending(path, '.zip', set()), root=self.session.lib.root)
        except OSError as e:
            self.dialogs.notice('error', 'Could not export diagnostics', str(e))
            return
        self.dialogs.notice('info', 'Export diagnostics', tr(
            'Saved {file}. Your folders, user name and speaker names are replaced in it. Open it and read it before you '
            'attach it to a bug report.').format(file=made))

    def _offer_transcript_setup(self):
        """Suggest from audio was pressed with no recognizer: say so, and offer the one-time setup that gets one."""
        self.session.bus.status(transcript.NOT_SET_UP)
        script = settings.program_dir() / transcript.SETUP_SCRIPT
        if script.is_file() and self.dialogs.confirm('Set up draft transcripts?', asr_setup.OFFER, yes='Open the setup',
                                                     no='Not now'):
            self.dialogs.open_path(script)

    def suggest_transcript(self):
        take = self.session.current
        if take is None:
            return
        if not self.session.drafts.available():
            self._offer_transcript_setup()
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
        try:
            current['outputs'] = list_outputs()
        except Exception:                           # no sound library, no devices: only Windows' default is offered
            current['outputs'] = []
        answer = self.dialogs.settings(current)
        if answer is None:
            return
        changes = {key: answer[key] for key in ('tail_seconds', 'ffmpeg', 'asr_python', 'asr_model')}
        changes['asr_language'] = answer.get('asr_language')
        changes['global_hotkey'] = True if answer.get('global_hotkey') else None             # off is the default: no entry
        changes['record_key'] = answer.get('record_key')
        changes['output_device'] = answer.get('output_device')
        changes['theme'] = None if answer.get('theme') in (None, settings.THEMES[0]) else answer['theme']
        changes['text_scale'] = answer.get('text_scale')
        changes['language'] = answer.get('language')
        settings.update(changes, s.settings_file)
        clean.set_ffmpeg_path(answer['ffmpeg'])
        s.transport.set_output(answer.get('output_device'))
        self._apply_record_key()
        s.capturing.set_tail(answer['tail_seconds'])
        if answer['root'] and Path(answer['root']) != s.lib.root:
            self._change_root(answer['root'])
        s.bus.notify('settings')
        if self.hotkey is not None:
            self._apply_hotkey()

    def _change_root(self, folder):
        """Use another output folder. With recordings in the old one and an empty new one, ask whether they should go too."""
        s = self.session
        workspace = s.workspace
        if settings.holds_recordings(s.lib.root) and not settings.holds_recordings(folder) and not workspace.has_unsaved_takes() \
                and self.dialogs.confirm('Move your recordings?', f'Your recordings are in {s.lib.root}. Move them to {folder}?'
                                         '\n\nEach file is copied and checked, and the old ones are removed once all of them '
                                         'have arrived. Choose Leave them to start with an empty folder.', yes='Move them',
                                         no='Leave them'):
            try:
                relocation = Relocation(s.lib.root, folder)
            except (ValueError, OSError) as e:
                self.dialogs.notice('warning', 'Move your recordings', str(e))
                return
            s.batch.relocate(relocation, lambda: workspace.set_root(folder, moved=True))
            return
        workspace.set_root(folder)

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
        menu.addAction('Rename', lambda: (self._take_picked(take), self.rename_take()))
        copy = menu.addAction('Copy file path', lambda: QApplication.clipboard().setText(str(take.export_path or take.master_path)))
        copy.setEnabled((take.export_path or take.master_path) is not None)
        if take.kept:
            menu.addAction('Save a copy...', lambda: (self._take_picked(take), self.save_copy()))
        others = [] if take.unsaved else self._other_speakers(take)
        if others:
            move = menu.addMenu('Move to speaker')
            for slug, name in others:
                move.addAction(name, lambda checked=False, slug=slug: self._move(take, slug))
        if take.changed:
            menu.addAction('Go back to the kept clip...', lambda: self.revert_take(take))
        menu.addSeparator()
        menu.addAction('Discard...', lambda: (self._take_picked(take), self.discard()))
        return menu

    def _take_menu(self, take, pos):
        if not self.session.capturing.recording and not self.session.batch.running:
            self.dialogs.popup(self.build_take_menu(take), pos)

    def build_selection_menu(self, takes):
        """The right-click menu of several selected takes."""
        menu = QMenu(self)
        count = len(takes)
        menu.addAction(f'Keep {count} takes...', lambda: self.keep_selected(takes))
        others = self._other_speakers(takes[0])
        if others:
            move = menu.addMenu(f'Move {count} takes to speaker')
            for slug, name in others:
                move.addAction(name, lambda checked=False, slug=slug: self.move_selected(takes, slug))
        menu.addSeparator()
        menu.addAction(f'Discard {count} takes...', lambda: self.discard_selected(takes))
        return menu

    def _selection_menu(self, takes, pos):
        if not self.session.capturing.recording and not self.session.batch.running:
            self.dialogs.popup(self.build_selection_menu(takes), pos)

    def build_speaker_menu(self):
        menu = QMenu(self)
        menu.addAction(self.actions.rename_speaker)
        menu.addAction(self.actions.speaker_details)
        menu.addAction(self.actions.new_speaker)
        menu.addSeparator()
        menu.addAction(self.actions.delete_speaker)
        return menu

    def _speaker_menu(self, pos):
        self.dialogs.popup(self.build_speaker_menu(), pos)

    # ---- dropping files ----------------------------------------------------------------------------------------------

    @staticmethod
    def _dropped_audio(mime):
        paths = [Path(url.toLocalFile()) for url in mime.urls() if url.isLocalFile()]
        return [p for p in paths if p.suffix.lower() in AUDIO_SUFFIXES]

    def dragEnterEvent(self, event):
        if self._dropped_audio(event.mimeData()) and not self.session.capturing.recording:
            event.acceptProposedAction()

    def dropEvent(self, event):
        self.import_files(self._dropped_audio(event.mimeData()))
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
            self.dialogs.notice('error', 'Could not save everything',
                                '\n'.join(failures) + '\n\nThe window stays open so nothing is lost. Free up space or '
                                'fix the folder, then close again.')
            event.ignore()
            return
        self.timer.stop()
        self._analysis_timer.stop()
        self._announcer.stop()          # a closed window must not read out its last status, 0.4 s on
        if self.hotkey is not None:
            self.hotkey.set_enabled(False)
        self.badge.close()
        self._save_geometry()
        self._shut = True
        event.accept()
        self.closed.emit()
