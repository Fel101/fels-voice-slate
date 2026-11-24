"""The main window: a thin view over a `Session`.

Nothing here decides anything. A click or a key calls a session method; the session reports what changed on its bus;
the window redraws the parts that heard about it. A timer calls `session.tick()` about twenty times a second, which
is also when the meter, the clock, the live waveform and the playhead are updated.
"""
import base64
from contextlib import contextmanager
from pathlib import Path

from PySide6.QtCore import QByteArray, QSize, QStandardPaths, Qt, QTimer, Signal
from PySide6.QtGui import QAccessible, QAccessibleAnnouncementEvent, QGuiApplication, QKeySequence
from PySide6.QtWidgets import QApplication, QLabel, QMainWindow, QMenu, QWidget

from .. import asr_setup, audio_import, dataset, handoff, launch, manifest, share, transcript
from .. import clean, settings
from ..audio_io import list_outputs
from ..clean import CleanError
from ..core import render
from ..core.edits import describe
from ..i18n import tr
from ..session import Session, run
from ..session.measure import take_key
from ..store import Backup, BringIn, KeepBlocked, Relocation, TrashUnavailable, clean_label
from .actions import SPEC, Actions, shortcut_text
from .badge import RecordingBadge
from .dialogs import Dialogs, file_filter
from .editor_panel import EditorPanel
from .inspector import Inspector
from .library import LibraryPanel
from .palette import PALETTE
from .roomcheck import RoomCheckDialog
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
LONG_TAKE_SECONDS = 120.0          # rendering a take this long can hold the window for more than half a second
SHORT_IMPORT_SECONDS = 300.0       # recordings this short in all are copied in at once; longer ones a block at a time


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
        self._close_after_handoff = False
        self._last_head = None
        self._edited_seconds = 0.0
        self.room_dialog = None                 # the room and level check, made the first time it is asked for
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
        self.badge.stopRequested.connect(self.session.record_pressed)          # stops a take, or ends a script run
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
        self._drafts_ready = False
        for item in (self.status_export, self.status_clean, self.status_drafts):
            item.setStyleSheet('padding-right: 14px;')
            bar.addPermanentWidget(item)

    def _build_menu(self):
        a, menu = self.actions, self.topbar.menu
        for group in (('settings', 'import_recording', 'new_speaker', 'rename_speaker', 'speaker_details', 'delete_speaker'),
                      ('move_take', 'save_copy', 'lab_handoff', 'export_manifest', 'export_dataset', 'split_take'),
                      ('keep_selected', 'discard_selected', 'move_selected', 'discard_others'),
                      ('load_script', 'hands_free', 'clear_script'),
                      ('open_folder', 'back_up', 'bring_in')):
            for name in group:
                menu.addAction(a[name])
            menu.addSeparator()
        menu.aboutToShow.connect(lambda: a.lab_handoff.setText(self._handoff_label()))
        menu.addAction(a.refresh_devices)
        menu.addAction(a.room_check)
        menu.addSeparator()
        help_menu = menu.addMenu(tr('Help'))
        help_menu.addAction(a.shortcuts)
        help_menu.addAction(a.check_for_updates)
        help_menu.addAction(a.about)
        menu.addAction(a.diagnostics)
        menu.addSeparator()
        menu.addAction(a.quit)

    def _connect(self):
        s, a = self.session, self.actions
        capturing, editor, transport, workspace = s.capturing, s.editor, s.transport, s.workspace
        a.record.triggered.connect(s.record_pressed)
        a.room_check.triggered.connect(self.room_check)
        a.arm.triggered.connect(lambda: capturing.toggle_arm())
        a.play.triggered.connect(self.toggle_play)
        a.stop.triggered.connect(transport.stop)
        a.loop.triggered.connect(lambda: transport.toggle_loop())
        a.ab.triggered.connect(lambda: self._busy_with('Getting the clip ready to play...', transport.ab))
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
        a.check_joins.triggered.connect(lambda: self._busy_with('Getting the clip ready to play...', transport.check_cuts))
        a.cut_start.triggered.connect(editor.cut_from_start)
        a.cut_end.triggered.connect(editor.cut_to_end)
        a.select_all.triggered.connect(self._select_all)
        a.set_in.triggered.connect(editor.set_in)
        a.set_out.triggered.connect(editor.set_out)
        a.marker.triggered.connect(self._toggle_marker)
        a.prev_marker.triggered.connect(lambda: self._jump_marker(False))
        a.next_marker.triggered.connect(lambda: self._jump_marker(True))
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
        a.lab_handoff.triggered.connect(lambda: self.lab_handoff())
        a.export_manifest.triggered.connect(self.export_manifest)
        a.open_folder.triggered.connect(self.open_folder)
        a.back_up.triggered.connect(self.back_up)
        a.bring_in.triggered.connect(self.bring_in)
        a.refresh_devices.triggered.connect(capturing.refresh_devices)
        a.load_script.triggered.connect(self.load_script)
        a.clear_script.triggered.connect(s.script.clear)
        a.prev_line.triggered.connect(lambda: s.script.step(-1))
        a.next_line.triggered.connect(lambda: s.script.step(1))
        a.next_open.triggered.connect(self.inspector.script._next)
        a.again.triggered.connect(s.run.again)
        a.hands_free.triggered.connect(self.hands_free)
        a.next_take.triggered.connect(lambda: self.step_take(1))
        a.prev_take.triggered.connect(lambda: self.step_take(-1))
        a.split_take.triggered.connect(self.split_take)
        a.keep_selected.triggered.connect(lambda: self.keep_selected())
        a.discard_selected.triggered.connect(lambda: self.discard_selected())
        a.discard_others.triggered.connect(self.discard_other_takes)
        a.move_selected.triggered.connect(lambda: self.move_selected())
        a.settings.triggered.connect(self.open_settings)
        a.shortcuts.triggered.connect(self.dialogs.shortcuts)
        a.check_for_updates.triggered.connect(self.dialogs.check_for_updates)
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
        panel.recordRequested.connect(s.record_pressed)      # as the record key: with the count-in on, a script run
        panel.importRequested.connect(self.import_recording)
        panel.retryRequested.connect(s.workspace.retry_root)
        panel.settingsRequested.connect(self.open_settings)
        panel.listen.currentChanged.connect(lambda i: transport.set_listen(('original', 'edited')[i]))
        panel.script.previousRequested.connect(lambda: s.script.step(-1))
        panel.script.nextRequested.connect(lambda: s.script.step(1))
        panel.script.handsFreeRequested.connect(self.hands_free)
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
        inspector.showLineTakesRequested.connect(lambda: self.library.show_takes('line'))

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
        self._show_status(tr('Something went wrong: {error}').format(error=text))
        if text not in self._errors_shown:
            self._errors_shown.add(text)
            self.dialogs.notice('error', 'Something went wrong', text + '\n\n' + tr(
                'The last action may not have finished. Your recordings are not affected. The details are in {log}').format(
                log=launch.log_path()))

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
        elif topics & {'recording', 'run', 'room'}:
            self.library.refresh_enabled()
        if 'room' in topics:
            self.topbar.refresh_recording()
        if topics & {'room', 'recording', 'devices'} and self.room_dialog is not None:
            self.room_dialog.refresh()          # whether it can start follows the recording and the source
        if 'devices' in topics:
            self.topbar.refresh_devices()
        if 'current' in topics:
            take = s.current
            panel.view.reset(take.seconds if take is not None else 0.0)
            if take is not None:
                s.measure.pyramid(take)             # first: the waveform is what the person looks at
                s.measure.spectrogram(take)
            self._measure_edited()
            self.inspector.refresh_edits()
            self._request_analysis()
            panel.info.refresh()
            panel.listen.set_current(0 if s.transport.listen == 'original' else 1)
        if 'script' in topics:
            self.library.refresh_choices()
        if topics & {'takes', 'current', 'recording', 'folder', 'script'}:
            self.library.refresh_takes()
        if 'meta' in topics and 'current' not in topics:
            self.inspector.refresh_meta(self._derived())
            self.library.refresh_takes()
            self._repaint_timelines()               # the markers are drawn on them
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
            self._repaint_timelines()               # the spectrogram, or a long take's waveform, may have arrived
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
        if topics & {'script', 'current', 'recording', 'takes', 'run', 'settings'}:
            panel.script.refresh()
        if topics & {'run', 'recording', 'script', 'settings'}:
            self._refresh_run()
        if topics & {'script', 'takes'}:
            self.inspector.refresh_script()
        if topics & {'current', 'edits', 'selection', 'meta', 'recording'}:
            panel.words.refresh()
        if 'batch' in topics:
            self.status_stop.setVisible(s.batch.running)
            self.status_stop.setToolTip(tr(s.batch.stop_tip))
            if self._close_after_handoff and not s.batch.running:
                self._close_after_handoff = False
                QTimer.singleShot(0, self, self.close)
        if 'settings' in topics:
            self._refresh_status_items()
        elif 'drafts' in topics:
            self._refresh_drafts_item()
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
        if not take.history.cuts:                   # all of it: no need for its mono mix, which takes a while on a long take
            self._edited_seconds = take.seconds
            return
        segments = render.segments_of(take.mono, take.rate, take.history.edits)
        self._edited_seconds = sum(b - a for a, b in segments) / take.rate

    def _repaint_timelines(self):
        panel = self.editor_panel
        panel.waveform.update()
        panel.spectrogram.update()
        panel.minimap.update()

    def _refresh_export_item(self):
        """What Keep writes for the open take, or for a new one when none is open: at the bottom of the window, and in the
        Keep command's tooltip. An Audition is kept at its own rate up to 48 kHz, and in 24 bits."""
        take = self.session.current
        rules = take.purpose if take is not None else self.session.workspace.purpose
        rate = take.export_rate if take is not None else rules.export_rate
        self.status_export.setText(tr('{purpose}: {rate} kHz · {bits}-bit · mono').format(
            purpose=tr(rules.name), rate=f'{rate / 1000:g}', bits=rules.bit_depth))
        tip = tr('Write the {rate} kHz, {bits}-bit WAV and its sidecar').format(rate=f'{rate / 1000:g}', bits=rules.bit_depth)
        self.actions.keep.setToolTip(f'{tip} ({shortcut_text(SPEC["keep"][2])})')
        self.actions.keep.setStatusTip(tip)

    def _refresh_status_items(self):
        self._drafts_ready = self.session.drafts.available()
        self.status_clean.setText(tr('Cleaning: FFmpeg ready' if clean.available() else 'Cleaning: FFmpeg not found'))
        self._refresh_drafts_item()

    def _refresh_drafts_item(self):
        """Whether drafts can be made, or, while several are waiting, how many: they are made one at a time."""
        ready, waiting = self._drafts_ready, self.session.drafts.waiting
        if waiting > 1:
            self.status_drafts.setText(tr('Draft transcripts: {count} waiting').format(count=waiting))
            self.status_drafts.setToolTip(tr('Drafts are made one at a time, the take in front first. Everything else goes on '
                                             'meanwhile, and closing the window does not wait for them.'))
            return
        self.status_drafts.setText(tr('Draft transcripts: ready' if ready else 'Draft transcripts: not set up'))
        self.status_drafts.setToolTip(tr('The speech recognizer chosen in Settings is ready.') if ready else
                                      f'{tr("Choose a speech recognizer in Settings (Ctrl+,). Settings file:")} '
                                      f'{self.session.settings_file}')

    def _refresh_run(self):
        """The record button, the large state of hands-free reading, and an empty live view while a run counts in."""
        s = self.session
        self.topbar.refresh_recording()
        live = self.editor_panel.live
        live.banner.show_state(s.run.state(), s.run.active, self._record_key())
        live.set_counting(s.run.phase == 'counting')
        if s.run.phase == 'counting':
            live.set_state(None, 0.0, s.workspace.purpose)

    def _update_mode(self):
        s = self.session
        if s.capturing.recording or s.run.active:
            mode, side = EditorPanel.LIVE, Inspector.RECORDING
        elif s.current is not None:
            mode, side = EditorPanel.EDIT, Inspector.REVIEW
        elif s.workspace.root_problem is not None:
            mode, side = EditorPanel.FOLDER, Inspector.IDLE
            self.editor_panel.out_of_reach.show_problem(s.workspace.out_of_reach_text())
        else:
            mode, side = EditorPanel.EMPTY, Inspector.IDLE
        self.editor_panel.set_mode(mode)
        self.inspector.set_mode(side)

    def _update_actions(self):
        s, a = self.session, self.actions
        take, editor = s.current, s.editor
        idle = not s.capturing.recording and not s.batch.running and not s.run.active and not s.room.active
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
            'marker': has_take or s.capturing.recording,
            'prev_marker': has_take and bool(take.meta.get('markers')),
            'next_marker': has_take and bool(take.meta.get('markers')),
            'play': has_take, 'stop': has_take and s.transport.active, 'loop': has_take, 'ab': has_take,
            'zoom_in': has_take, 'zoom_out': has_take, 'fit': has_take,
            'keep': has_take, 'discard': has_take, 'rerecord': has_take, 'rename': has_take,
            'import_recording': idle, 'new_speaker': idle, 'settings': idle, 'arm': idle, 'record': not s.batch.running,
            'split_take': has_take, 'next_take': idle and bool(s.workspace.takes), 'prev_take': idle and bool(s.workspace.takes),
            'keep_selected': idle and len(chosen) > 1, 'discard_selected': idle and len(chosen) > 1,
            'discard_others': has_take and bool(self._other_takes_of_line()),
            'move_selected': idle and len(chosen) > 1 and len(s.workspace.speakers) > 1,
            'rename_speaker': idle and s.workspace.speaker is not None,
            'speaker_details': idle and s.workspace.speaker is not None,
            'delete_speaker': idle and s.workspace.speaker is not None,
            'export_dataset': idle and s.workspace.speaker is not None,
            'move_take': has_take and not take.unsaved and len(s.workspace.speakers) > 1,
            'save_copy': has_take and take.kept,
            'lab_handoff': idle and (all(item.kept for item in chosen) if len(chosen) > 1 else take is not None and take.kept),
            'export_manifest': idle and s.workspace.speaker is not None,
            'prev_line': idle and bool(s.script.lines) and s.script.index > 0,
            'next_line': idle and bool(s.script.lines) and s.script.index < len(s.script.lines) - 1,
            'next_open': idle and bool(s.script.lines),
            'clear_script': bool(s.script.lines) and not s.run.active,
            'again': bool(s.script.lines) and not s.batch.running and not s.room.active, 'hands_free': idle,
            'room_check': idle or s.room.active,
            'back_up': idle and bool(s.workspace.speakers), 'bring_in': idle and not s.workspace.speakers,
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
        if s.run.active:
            self.topbar.set_time(0.0, tr('script run'))
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
            self.editor_panel.live.set_state(rows, elapsed, rules, capturing.markers)
            self.library.set_live(rows, elapsed)
            sub = tr('stopping...') if capturing.finishing else \
                tr('recording  ·  max {time}').format(time=format_clock(rules.record_limit)[:5])
            self.topbar.set_time(elapsed, sub)
            self.topbar.meter.set_levels(*capturing.level)
            self._refresh_badge()
            return
        if s.room.active:                           # the check has the microphone: its meter is live
            self.topbar.meter.set_levels(*capturing.level)
            if self.room_dialog is not None:
                self.room_dialog.tick()
        if s.run.active:                            # between the takes of a run the microphone is open: the sign stays
            self._refresh_badge()
        elif self.badge.isVisible():
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
        """A recording that runs while this window is not the one in front has a sign of its own on top of everything, and
        so does a script run, whose microphone is open between its takes too."""
        if self.isActiveWindow():
            self.badge.hide()
            return
        s, key = self.session, self._record_key()
        text = None
        if s.run.phase == 'counting':
            text = tr('Script run  ·  next line in {seconds}  ·  {key} ends').format(seconds=s.run.count_left, key=key)
        elif s.run.active:
            text = tr('Recording line {line}  {time}  ·  {key} ends the run').format(
                line=s.script.index + 1, time=format_clock(s.capturing.elapsed)[:5], key=key)
        self.badge.show_for(s.capturing.elapsed, key, text)

    def _apply_record_key(self):
        """Put the record key from the settings on the Record command and everything that names it."""
        key = self._record_key()
        self.actions.set_record_key(key)
        again = run.again_key(key)
        self.actions.again.setShortcut(QKeySequence(again))
        self.actions.again.setToolTip(f'{tr(SPEC["again"][3])} ({again})')
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
            self.session.bus.status(tr('{key} only works while this window is in front: {problem}.').format(
                key=key.name, problem=key.problem))

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
        if self.session.room.active:
            self.session.room.end()
        elif self.session.run.active:
            self.session.run.end()
        elif self.session.transport.active:
            self.session.transport.stop()
        else:
            self.session.editor.clear_selection()

    def room_check(self):
        """Show the room and level check, which does not block the window. It starts when the person presses Start."""
        if self.room_dialog is None:
            self.room_dialog = RoomCheckDialog(self.session, self)
        self.room_dialog.refresh()
        self.room_dialog.show()
        self.room_dialog.raise_()
        self.room_dialog.activateWindow()

    def hands_free(self):
        s = self.session
        answer = self.dialogs.hands_free(dict(s.run.options), self._record_key())
        if answer is not None:
            s.run.set_options(**answer)

    def _clicked(self, t):
        self.session.editor.clear_selection()
        self.session.transport.seek(t)

    def _toggle_marker(self):
        """M: while recording, mark the newest sound; else mark the moment being heard, or the playhead, or take away the
        marker there."""
        s = self.session
        if s.capturing.recording:
            s.capturing.add_marker()
            return
        head = s.transport.head
        s.editor.toggle_marker(s.editor.playhead if head is None else head)

    def _jump_marker(self, forward):
        """Alt+Left and Alt+Right: the playhead, and the sound if it is playing, go to the marker before or after it."""
        s = self.session
        head = s.transport.head
        moment = s.editor.marker_beside(s.editor.playhead if head is None else head, forward)
        if moment is not None:
            s.transport.seek(moment)
            self.editor_panel.view.ensure_visible(moment)
        s.editor.went_to_marker(moment, forward)

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
        if self.session.run.active:
            self.session.bus.status('End the script run before opening another take.')
            return
        if self.session.capturing.recording:
            self.session.bus.status('Stop the recording before opening another take.')
            return
        if self.session.batch.running:
            self.session.bus.status('Wait for this to finish, or press Stop, before opening another take.')
            return
        with self._busy('Opening the take...', take):
            if self.session.workspace.open_take(take) and take.seconds >= LONG_TAKE_SECONDS:
                take.mono                           # worked out now, under the busy cursor, not when it is first drawn

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
        error = None
        with self._busy('Keeping the take...'):
            try:
                s.keep()
            except (KeepBlocked, CleanError, OSError) as e:
                error = e
        if isinstance(error, KeepBlocked):              # said once the busy cursor is gone, not under it
            self.dialogs.notice('warning', 'Cannot keep yet', '\n'.join(f'- {p}' for p in error.problems))
        elif isinstance(error, CleanError):
            self.dialogs.notice('error', 'Could not clean the audio', str(error))
        elif isinstance(error, OSError):
            self.dialogs.notice('error', 'Could not write the files', str(error))

    def toggle_play(self):
        """Space: pause, resume, or play the edited clip. Getting a long take ready to play can take a second or two."""
        transport = self.session.transport
        if transport.playing or transport.paused:
            transport.toggle()
            return
        self._busy_with('Getting the clip ready to play...', transport.toggle)

    @contextmanager
    def _busy(self, text, take=None):
        """While a long take (the one in front, unless another is given) is worked on, the wait cursor, and `text` in the
        status line until the work says what it did. A short take is quick enough to need neither."""
        take = take or self.session.current
        if take is None or take.seconds < LONG_TAKE_SECONDS:
            yield
            return
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        self._show_status(text)
        self.status_text.repaint()          # drawn now: the window does not get back to drawing until the work is done
        try:
            yield
        finally:
            QApplication.restoreOverrideCursor()
            if self.status_text.full_text == tr(text):      # the work said nothing: the line goes back to what it said
                self._show_status(self.session.bus.status_text)

    def _busy_with(self, text, action):
        with self._busy(text):
            action()

    def _discard_current(self, title, text):
        """Ask, then move the take's files to the Recycle Bin (or delete them if the person insists). True if it is gone."""
        if not self.dialogs.confirm(title, text, yes='Move to Recycle Bin'):
            return False
        workspace = self.session.workspace
        try:
            return workspace.discard()
        except TrashUnavailable as e:
            if self.dialogs.confirm('The Recycle Bin is not available',
                                    f'{e}\n\n' + tr('Delete the files for good instead? This cannot be undone.'),
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
        also = ' ' + tr('{count} of them are kept clips, and their WAV files and sidecars go too.' if kept != 1 else
                        '{count} of them is a kept clip, and its WAV file and sidecar go too.').format(count=kept) if kept else ''
        if self.dialogs.confirm(tr('Discard {count} takes?').format(count=len(takes)),
                                tr('They move to the Recycle Bin, with their original recordings.') + also + ' ' +
                                tr('You can restore them from there.'), yes='Move to Recycle Bin'):
            s.batch.discard(takes)

    def _other_takes_of_line(self, take=None):
        """The takes of the script line of `take` (the open take) that are not kept, apart from that take."""
        take = take or self.session.current
        line = take.meta.get('script_line') if take is not None else None
        if not line:
            return []
        return [other for other in self.session.workspace.takes
                if other is not take and not other.kept and other.meta.get('script_line') == line]

    def discard_other_takes(self):
        """Having chosen the take of a line, send the rest of its takes to the Recycle Bin, after one question. Kept clips
        stay: a kept clip goes only when it is discarded on its own."""
        s = self.session
        others = self._other_takes_of_line()
        if not others or s.capturing.recording or s.batch.running or s.run.active:
            return
        count, line = len(others), s.current.meta['script_line']
        shown = line if len(line) <= 60 else line[:57] + '...'
        if count == 1:
            title = tr('Discard the other take of this line?')
            text = tr('"{line}"\n\nIt moves to the Recycle Bin, with its original recording, and you can restore it from '
                      'there. The take open stays, and so does any kept clip of the line.')
        else:
            title = tr('Discard the {count} other takes of this line?')
            text = tr('"{line}"\n\nThey move to the Recycle Bin, with their original recordings, and you can restore them '
                      'from there. The take open stays, and so does any kept clip of the line.')
        if self.dialogs.confirm(title.format(count=count), text.format(line=shown), yes='Move to Recycle Bin'):
            s.batch.discard(others)

    def move_selected(self, takes=None, slug=None):
        """File the selected takes under another speaker, asking which unless `slug` says."""
        s = self.session
        takes = self.library.selected_takes() if takes is None else takes
        if not takes or s.capturing.recording or s.batch.running:
            return
        if slug is None:
            others = self._other_speakers(takes[0])
            index = self.dialogs.choose_item('Move takes', tr('File these {count} takes under:').format(count=len(takes)),
                                             [name for _, name in others]) if others else None
            if index is None:
                return
            slug = others[index][0]
        kept = sum(1 for take in takes if take.kept)
        if kept and not self.dialogs.confirm(
                'Move these kept clips?',
                tr('{count} kept clip will be filed under {speaker}. Permission, origin and allowed use go with it, and its '
                   'saved description will say where it was moved from.' if kept == 1 else
                   '{count} kept clips will be filed under {speaker}. Permission, origin and allowed use go with each one, '
                   'and its saved description will say where it was moved from.').format(
                    count=kept, speaker=s.workspace.name_of(slug)), yes='Move'):
            return
        s.batch.move(takes, slug)

    def split_take(self):
        """Make a new take of each part of the open take, after saying how many there would be."""
        s = self.session
        take = s.current
        if take is None or s.capturing.recording or s.batch.running:
            return
        with self._busy('Looking for the pauses...'):
            ranges = s.workspace.split_ranges(take)
        if len(ranges) < 2:
            self.dialogs.notice('info', 'Split at pauses', tr('There is nowhere to split this take: it has no pause of at '
                                                              'least a third of a second after its first {seconds} '
                                                              'seconds.').format(seconds=f'{take.purpose.best_from:g}'))
            return
        lengths = ', '.join(f'{(end - first) / take.rate:.0f}' for first, end in ranges[:8])
        lengths += ', ...' if len(ranges) > 8 else ''
        if self.dialogs.confirm('Split this take?', tr('It will become {count} new takes, cut in the middle of its pauses '
                                                       '({lengths} seconds). This take stays as it is, and the new takes start '
                                                       'from the original recording, without its edits.').format(
                                    count=len(ranges), lengths=lengths),
                                yes=tr('Make {count} takes').format(count=len(ranges))):
            s.batch.split(take, ranges)         # a part at a time, with Stop: a long take makes hundreds

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
        """Copy recordings in as takes, the last one in front. A few short ones are copied at once, under the busy cursor.
        More than that, or a format FFmpeg has to write out first (its length is not known until then), are copied a block
        at a time, with how far it has got in the status line and Stop. A recording long enough to slow everything down is
        asked about first; one too long for this computer's memory is refused, with what to do. One that cannot be read is
        said in a notice."""
        s = self.session
        paths = list(paths)
        if paths and s.run.active:
            s.bus.status('End the script run before importing.')
            return
        if not paths or s.capturing.recording:
            return
        if s.batch.running:                         # dropped on the window while something else is under way
            s.bus.status('Wait for this to finish, or press Stop, before importing more.')
            return
        wanted, seconds, unknown = [], 0.0, False
        for path in paths:
            try:
                shape = audio_import.shape_of(path)
            except (RuntimeError, OSError):         # the import says what is wrong with it
                wanted.append(path)
                continue
            if shape is None:
                unknown = True
            else:
                fit = audio_import.fit(*shape)
                if fit.verdict == 'warn' and not self._long_import_wanted(Path(path).name, fit):
                    continue
                seconds += fit.seconds if fit.verdict != 'refuse' else 0.0
            wanted.append(path)
        if not wanted:
            return
        if unknown or seconds > SHORT_IMPORT_SECONDS:
            s.batch.import_files(wanted)
            return
        paths = wanted
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        imported = 0
        try:
            for path in paths:
                if self.session.workspace.import_file(path) is not None:
                    imported += 1
                elif self.session.workspace.root_problem is not None:
                    break                   # the folder cannot be reached: one message says so, not one for each file
        finally:
            QApplication.restoreOverrideCursor()
        if imported > 1:
            self.session.bus.status(tr('Imported {count} recordings. Edit each one, then Keep.').format(count=imported))

    def _long_import_wanted(self, name, fit):
        """Ask before importing a recording long enough to make every step slow, and to use much of the memory."""
        length = audio_import.duration_text(fit.seconds)
        if fit.memory:
            text = tr('{name} is {length} long. Working on it will use about {needed} GB of memory, of the {memory} GB this '
                      'computer has, and measuring, playing and keeping it will each take a while. Import it?').format(
                name=name, length=length, needed=f'{fit.needed / 2**30:.1f}', memory=f'{fit.memory / 2**30:.0f}')
        else:
            text = tr('{name} is {length} long, and measuring, playing and keeping it will each take a while. Import '
                      'it?').format(name=name, length=length)
        return self.dialogs.confirm('Import a long recording?', text, yes='Import it', no='Leave it out')

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
        what = (tr('{count} kept clip' if kept == 1 else '{count} kept clips').format(count=kept),
                tr('{count} other take' if unkept == 1 else '{count} other takes').format(count=unkept))
        if not self.dialogs.confirm(tr('Delete {name}?').format(name=name),
                                    tr('Their folder moves to the Recycle Bin: {kept}, {other}, the original recordings, and '
                                       'their release. You can restore it from there, but {name} leaves the list of '
                                       'speakers.').format(kept=what[0], other=what[1], name=name),
                                    yes='Move to Recycle Bin', danger=True):
            return
        try:
            workspace.delete_speaker(slug)
        except TrashUnavailable as e:
            if self.dialogs.confirm('The Recycle Bin is not available',
                                    f'{e}\n\n' + tr('Delete the folder for good instead? This cannot be undone.'),
                                    yes='Delete permanently', danger=True):
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
                tr('The clip will be filed under {speaker}. Its permission, origin and allowed use go with it, and its saved '
                   'description will say it was moved from {before}.').format(speaker=workspace.name_of(slug),
                                                                              before=workspace.name_of(take.speaker)),
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
            self.dialogs.notice('error', 'Save a copy', tr('The kept clip {file} is not in its folder any more.').format(
                file=Path(take.export_path).name))
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
                                             file_filter(text, '*' + ending))
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
        s.bus.status(tr('Saved a copy ({rate}) to {path}').format(rate=share.rate_text(rate), path=path))

    def _handoff_label(self):
        return tr('Send to Voice Lab...' if handoff.inbox_exists(self.session.lib.root) else 'Save for Voice Lab...')

    def lab_handoff(self, takes=None):
        s = self.session
        if s.capturing.recording or s.batch.running or s.run.active or s.room.active:
            return
        if takes is None:
            selected = self.library.selected_takes()
            takes = selected if len(selected) > 1 else [s.current]
        if not takes or not all(take is not None and take.kept for take in takes):
            return
        sending = handoff.inbox_exists(s.lib.root)
        clips = [handoff.Clip.from_take(take) for take in takes]

        def checked(results, stopped, failed):
            if not stopped:
                QTimer.singleShot(0, self, lambda: self._write_handoff(results, takes, sending))
            elif not failed:
                s.bus.status(tr('Voice Lab handoff stopped.'))

        s.batch.handoff('Checking clips for Voice Lab', clips, s.jobs, handoff.Clip.check, checked)

    def _write_handoff(self, clips, takes, sending):
        s = self.session
        if self._shut or s.batch.running or s.capturing.recording or s.run.active or s.room.active:
            return
        draft = next((take for take, clip in zip(takes, clips) if clip.draft), None)
        if draft is not None:
            answer = self.dialogs.handoff_draft(sending)
            if answer == 'review':
                self._take_picked(draft)
                self.inspector.show_tab(Inspector.CLIP_TAB)
                self.inspector.clip.transcript.setFocus()
            if answer != 'proceed':
                s.bus.status(tr('Voice Lab handoff stopped.'))
                return
        destinations, folder = {}, None
        if not sending:
            if not self.dialogs.handoff_save(len(clips)):
                s.bus.status(tr('Voice Lab handoff stopped.'))
                return
            if len(clips) == 1:
                path = self.dialogs.handoff_destination(clips[0].name + handoff.EXTENSION)
                if not path:
                    s.bus.status(tr('Voice Lab handoff stopped.'))
                    return
                destinations[clips[0]] = self._with_ending(path, handoff.EXTENSION, {handoff.EXTENSION})
            else:
                # One folder for all of them. A name already there gets -2, -3 ..., so nothing is asked twice.
                folder = self.dialogs.choose_folder('Choose a folder for the Voice Lab files')
                if not folder:
                    s.bus.status(tr('Voice Lab handoff stopped.'))
                    return
                try:
                    handoff.check_folder(folder, s.lib.root)
                except handoff.HandoffError as error:
                    s.bus.notice('warning', 'Save for Voice Lab', str(error))
                    s.bus.status(tr('Voice Lab handoff stopped.'))
                    return

        def finished(paths, stopped, _failed):
            if stopped:
                text = (tr('Handoff stopped. Sent {count} of {total} clips to Voice Lab.') if sending else
                        tr('Handoff stopped. Saved {count} of {total} files for Voice Lab.')).format(
                            count=len(paths), total=len(clips))
            elif sending:
                text = tr('Sent 1 clip to Voice Lab. Open the Cast page in Voice Lab to add it.' if len(paths) == 1 else
                          'Sent {count} clips to Voice Lab. Open the Cast page in Voice Lab to add them.').format(
                              count=len(paths))
            elif folder is None:
                text = tr('Saved 1 file for Voice Lab. In Cast, choose Import from Voice Slate.')
            else:
                text = tr('Saved {count} files for Voice Lab in {folder}. In Cast, choose Import from Voice Slate for each '
                          'file.').format(count=len(paths), folder=folder)
            s.bus.status(text)
            if not stopped:
                s.bus.notice('info', 'Send to Voice Lab' if sending else 'Save for Voice Lab', text)

        library = s.lib.root
        s.batch.handoff('Sending clips to Voice Lab' if sending else 'Saving files for Voice Lab', clips, s.jobs,
                        lambda clip: clip.write(library, destinations.get(clip), folder=folder), finished)

    def _export_totals(self, slug):
        """(clips, seconds) a manifest or dataset of this speaker, and of all speakers, would hold, from the sidecars."""
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            return {'speaker': manifest.tally(self.session.lib, [slug]), 'all': manifest.tally(self.session.lib)}
        except OSError:
            return None
        finally:
            QApplication.restoreOverrideCursor()

    def export_manifest(self):
        """List the kept clips of this speaker, or of everyone, in a CSV or JSON Lines file."""
        s = self.session
        workspace = s.workspace
        slug = workspace.speaker
        if slug is None or s.capturing.recording:
            return
        choice = self.dialogs.manifest_options(workspace.name_of(slug), self._export_totals(slug))
        if choice is None:
            return
        scope, kind = choice
        text, ending = manifest.FORMATS[kind]
        known = {entry[1] for entry in manifest.FORMATS.values()}
        folder = s.lib.folder(slug) if scope == 'speaker' and s.lib.folder(slug).is_dir() else s.lib.root
        name = f'{slug}-manifest' if scope == 'speaker' else 'manifest'
        path = self.dialogs.choose_save_file('Export manifest', folder / (name + ending), file_filter(text, '*' + ending))
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
            self.dialogs.notice('info', 'Export manifest', tr('There are no kept clips to list yet.') +
                                ('\n\n' + tr('Left out:') + f'\n{detail}' if detail else ''))
            return
        listed = tr('Listed {count} clip in {path}' if len(rows) == 1 else 'Listed {count} clips in {path}').format(
            count=len(rows), path=path)
        s.bus.status(listed + ('. ' + tr('{count} left out.').format(count=len(left_out)) if left_out else ''))
        if left_out:
            self.dialogs.notice('warning', 'Some clips were left out',
                                tr('{count} clip is in the manifest. These are not:' if len(rows) == 1 else
                                   '{count} clips are in the manifest. These are not:').format(count=len(rows)) +
                                f'\n\n{detail}')

    def export_dataset(self):
        """Gather the kept clips in a new folder, a few at a time, so the window stays alive and it can be stopped."""
        s = self.session
        workspace = s.workspace
        slug = workspace.speaker
        if slug is None or s.capturing.recording or s.batch.running:
            return
        choice = self.dialogs.dataset_options(workspace.name_of(slug), self._export_totals(slug))
        if choice is None:
            return
        scope, layout, rate, originals, validation = choice
        folder = self.dialogs.choose_folder('Choose an empty folder for the dataset', '')
        if not folder:
            return
        try:
            export = dataset.Export(s.lib, folder, layout, rate, originals, validation)
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
        s.batch.export_dataset(export, export.split(rows))
        s.batch.left_out.extend(left_out)

    def _backup_refused(self, title):
        """Why a backup cannot be made or brought in now, said to the person, or False when it can."""
        s = self.session
        if s.capturing.recording or s.batch.running:
            return True
        if s.workspace.check_root() is not None:
            self.dialogs.notice('error', title, s.workspace.out_of_reach_text())
            return True
        if s.workspace.has_unsaved_takes():
            self.dialogs.notice('warning', title, 'A take is only in memory, because its original could not be written yet. '
                                                  'Wait until it is saved, then try again.')
            return True
        return False

    def back_up(self):
        """Copy the whole output folder to a place the person chooses, each file checked, as a job that can be stopped."""
        s = self.session
        if self._backup_refused('Back up recordings'):
            return
        folder = self.dialogs.choose_folder('Choose an empty folder for the backup', '')
        if not folder:
            return
        try:
            backup = Backup(s.lib.root, folder)
        except (ValueError, OSError) as e:
            self.dialogs.notice('warning', 'Back up recordings', str(e))
            return
        s.batch.back_up(backup)

    def bring_in(self):
        """Copy a backup into the output folder while it has no recordings, each file checked, as a job that can be
        stopped. The window shows what came in once all of it has."""
        s = self.session
        workspace = s.workspace
        if self._backup_refused('Bring in a backup'):
            return
        if workspace.speakers:
            self.dialogs.notice('info', 'Bring in a backup', 'Bring a backup into an output folder that has no recordings '
                                                             'yet: choose an empty one in Settings > Output folder first.')
            return
        folder = self.dialogs.choose_folder('Choose the backup to bring in', '')
        if not folder:
            return
        try:
            bringing = BringIn(folder, s.lib.root)
        except (ValueError, OSError) as e:
            self.dialogs.notice('warning', 'Bring in a backup', str(e))
            return
        text = tr('Its {count} files are copied into the output folder, {folder}, and each one is checked. The backup '
                  'itself is not changed.').format(count=len(bringing.files), folder=s.lib.root)
        if not self.dialogs.confirm('Bring in this backup?', text, yes='Bring it in'):
            return
        root = s.lib.root
        s.batch.bring_in(bringing, lambda: workspace.set_root(root, moved=True))

    def open_folder(self):
        s = self.session
        if s.workspace.check_root() is not None:
            self.dialogs.notice('error', 'Could not open the folder', s.workspace.out_of_reach_text())
            return
        folder = s.lib.folder(s.workspace.speaker) if s.workspace.speaker else s.lib.root
        try:
            folder.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            self.dialogs.notice('error', 'Could not open the folder', str(e))
            return
        self.dialogs.open_path(folder)

    def load_script(self):
        path = self.dialogs.choose_file('Load a script', self.session.lib.root, file_filter('Text files', '*.txt', all_files=True))
        if path:
            self.session.script.load_file(path)

    def export_diagnostics(self):
        """Save a zip for a bug report, where the person chooses: what is installed, the settings and the end of the log,
        with their folders, user name and speakers' names replaced (diagnostics.py)."""
        from datetime import date

        from .. import diagnostics

        desktop = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DesktopLocation) or str(Path.home())
        start = Path(desktop) / f'voice-slate-diagnostics-{date.today()}.zip'
        path = self.dialogs.choose_save_file('Export diagnostics', start, file_filter('Zip files', '*.zip', all_files=True))
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
        self.session.bus.status(transcript.not_set_up())
        script = settings.program_dir() / transcript.SETUP_SCRIPT
        if script.is_file() and self.dialogs.confirm('Set up draft transcripts?', asr_setup.offer(), yes='Open the setup',
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
            current['outputs'], current['outputs_every'] = list_outputs(), list_outputs(every=True)
        except Exception:                           # no sound library, no devices: only Windows' default is offered
            current['outputs'] = current['outputs_every'] = []
        answer = self.dialogs.settings(current)
        if answer is None:
            return
        changes = {key: answer[key] for key in ('tail_seconds', 'ffmpeg', 'asr_python', 'asr_model')}
        changes['asr_language'] = answer.get('asr_language')
        changes['global_hotkey'] = True if answer.get('global_hotkey') else None             # off is the default: no entry
        changes['record_key'] = answer.get('record_key')
        changes['output_device'] = answer.get('output_device')
        every = bool(answer.get('all_interfaces'))
        changes['all_interfaces'] = True if every else None                                    # off is the default: no entry
        changes['theme'] = None if answer.get('theme') in (None, settings.THEMES[0]) else answer['theme']
        changes['text_scale'] = answer.get('text_scale')
        changes['language'] = answer.get('language')
        settings.update(changes, s.settings_file)
        clean.set_ffmpeg_path(answer['ffmpeg'])
        s.transport.set_output(answer.get('output_device'), every)
        if every != settings.every_interface(current):
            s.capturing.refresh_devices()
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
                and self.dialogs.confirm('Move your recordings?',
                                         tr('Your recordings are in {old}. Move them to {new}?').format(old=s.lib.root,
                                                                                                     new=folder) + '\n\n' +
                                         tr('Each file is copied and checked, and the old ones are removed once all of them '
                                            'have arrived. Choose Leave them to start with an empty folder.'),
                                         yes='Move them', no='Leave them'):
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
                action = menu.addAction(tr('Remove: {edit}').format(edit=describe(edit)))
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
            menu.addAction(self._handoff_label(), lambda: self.lab_handoff([take]))
        others = [] if take.unsaved else self._other_speakers(take)
        if others:
            move = menu.addMenu('Move to speaker')
            for slug, name in others:
                move.addAction(name, lambda checked=False, slug=slug: self._move(take, slug))
        if take.changed:
            menu.addAction('Go back to the kept clip...', lambda: self.revert_take(take))
        menu.addSeparator()
        menu.addAction('Discard...', lambda: (self._take_picked(take), self.discard()))
        if self._other_takes_of_line(take):
            menu.addAction(self.actions.discard_others.text(), lambda: (self._take_picked(take), self.discard_other_takes()))
        return menu

    def _take_menu(self, take, pos):
        if not self.session.capturing.recording and not self.session.batch.running:
            self.dialogs.popup(self.build_take_menu(take), pos)

    def build_selection_menu(self, takes):
        """The right-click menu of several selected takes."""
        menu = QMenu(self)
        count = len(takes)
        menu.addAction(tr('Keep {count} takes...').format(count=count), lambda: self.keep_selected(takes))
        if all(take.kept for take in takes):
            menu.addAction(self._handoff_label(), lambda: self.lab_handoff(takes))
        others = self._other_speakers(takes[0])
        if others:
            move = menu.addMenu(tr('Move {count} takes to speaker').format(count=count))
            for slug, name in others:
                move.addAction(name, lambda checked=False, slug=slug: self.move_selected(takes, slug))
        menu.addSeparator()
        menu.addAction(tr('Discard {count} takes...').format(count=count), lambda: self.discard_selected(takes))
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
        if self.session.batch.finishing_handoff:
            self._close_after_handoff = True
            self.session.batch.cancel()
            event.ignore()
            return
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
