"""The right panel: the clip's text fields, the processing chain, the measurements, and the Keep checklist."""
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (QCheckBox, QFrame, QLabel, QLineEdit, QListWidget, QListWidgetItem, QPlainTextEdit,
                               QScrollArea, QSizePolicy, QSlider, QStackedWidget, QWidget)

from .. import audio_core as ac
from .. import clean
from ..core import chain as chain_mod
from ..store import LABEL_MAX
from . import theme
from .palette import PALETTE, color
from .widgets import Segmented, Switch, button, caption, card, chip, clear_layout, hbox, label, set_chip, vbox


# ---- the Clip page ----------------------------------------------------------------------------------------------------

def transcript_state(take):
    """The chip beside the transcript: what kind of text it is and whether it needs a second look."""
    if take.draft_request is not None:
        return 'Drafting...', 'violet'
    text = take.meta['transcript'].strip()
    if take.needs_transcript_review:
        return 'Check after cuts', 'amber'
    draft = str(take.meta.get('draft_text') or '').strip()
    if draft and text == draft:
        return 'Draft: check it', 'amber'
    line = take.meta.get('script_line')
    if line and text == line.strip():
        return 'From script', 'violet'
    return '', None


class ClipPage(QWidget):
    """The transcript and the statements that go with the clip. Typing writes to the take as it happens."""

    suggestRequested = Signal()
    copyRequested = Signal(str)

    def __init__(self, session, parent=None):
        super().__init__(parent)
        self.session = session
        self._loading = False
        layout = vbox(self, spacing=8)
        self.name_edit = QLineEdit()                    # no caption of its own: the page has no room to spare
        self.name_edit.setAccessibleName('Label')
        self.name_edit.setPlaceholderText('Name this take (optional)')
        self.name_edit.setToolTip('A label for this take. It is shown in the list, and the filter box above the list '
                                  'searches it (F2)')
        self.name_edit.setMaxLength(LABEL_MAX)
        self.name_edit.textEdited.connect(lambda text: self._write(label=text))
        layout.addWidget(self.name_edit)
        head = hbox(spacing=8)
        head.addWidget(caption('Transcript'))
        head.addStretch(1)
        self.state = chip('', 'amber')
        self.state.hide()
        head.addWidget(self.state, 0, Qt.AlignmentFlag.AlignVCenter)
        layout.addLayout(head)
        self.transcript = QPlainTextEdit()
        self.transcript.setPlaceholderText('What is said in the clip, word for word.')
        self.transcript.setFixedHeight(96)
        self.transcript.setTabChangesFocus(True)
        self.transcript.textChanged.connect(self._transcript_edited)
        layout.addWidget(self.transcript)
        row = hbox(spacing=8)
        self.suggest = button('Suggest from audio', None, 'mic',
                              'Draft the transcript from the clip with the optional speech recognizer (see Settings)')
        self.suggest.clicked.connect(self.suggestRequested)
        row.addWidget(self.suggest)
        row.addStretch(1)
        self.counter = label('0 / 600', 'dim')
        row.addWidget(self.counter)
        layout.addLayout(row)
        layout.addSpacing(4)
        self.origin = self._field(layout, 'Origin', 'Where the recording came from', 'origin')
        self.allowed = self._field(layout, 'Allowed use', 'What the voice may be used for', 'allowed_use')
        self.source_url = self._field(layout, 'Source URL (optional)', 'A link to the original, if there is one',
                                      'source_url')
        layout.addSpacing(4)
        row = hbox(spacing=10)
        self.consent = QCheckBox()
        self.consent.setToolTip('Required before a clip can be kept')
        self.consent.toggled.connect(lambda on: self._write(consent_confirmed=on))
        row.addWidget(self.consent, 0, Qt.AlignmentFlag.AlignTop)
        text = label('I have the right to use this voice for the purpose stated above: it is my own voice, or the '
                     'speaker has given permission.', 'muted', wrap=True)
        row.addWidget(text, 1)
        layout.addLayout(row)
        layout.addSpacing(4)
        row = hbox(spacing=4)
        self.copy_text = button('Copy transcript', 'ghost', 'copy')
        self.copy_text.clicked.connect(lambda: self.copyRequested.emit('transcript'))
        self.copy_path = button('Copy file path', 'ghost', 'copy', 'The kept WAV file')
        self.copy_path.clicked.connect(lambda: self.copyRequested.emit('path'))
        row.addWidget(self.copy_text)
        row.addWidget(self.copy_path)
        row.addStretch(1)
        layout.addLayout(row)
        layout.addStretch(1)

    def _field(self, layout, name, placeholder, key):
        layout.addWidget(caption(name))
        edit = QLineEdit()
        edit.setPlaceholderText(placeholder)
        edit.textEdited.connect(lambda text: self._write(**{key: text}))
        layout.addWidget(edit)
        return edit

    def _write(self, **fields):
        if not self._loading:
            self.session.workspace.set_meta(**fields)

    def _transcript_edited(self):
        self._write(transcript=self.transcript.toPlainText())
        self._count()

    def _count(self):
        n = len(self.transcript.toPlainText().strip())
        self.counter.setText(f'{n} / {ac.TRANSCRIPT_MAX}')
        ok = ac.TRANSCRIPT_MIN <= n <= ac.TRANSCRIPT_MAX
        self.counter.setStyleSheet('' if ok or n == 0 else f'color: {PALETTE["amber"]};')

    def refresh(self):
        take = self.session.current
        self.setEnabled(take is not None)
        self._loading = True
        meta = take.meta if take is not None else {}
        for edit, key in ((self.origin, 'origin'), (self.allowed, 'allowed_use'), (self.source_url, 'source_url')):
            value = str(meta.get(key) or '')
            if edit.text() != value:
                edit.setText(value)
        name = str(meta.get('label') or '')
        if self.name_edit.text() != name:
            self.name_edit.setText(name)
        value = str(meta.get('transcript') or '')
        if self.transcript.toPlainText() != value:
            self.transcript.setPlainText(value)
        if self.consent.isChecked() != bool(meta.get('consent_confirmed')):
            self.consent.setChecked(bool(meta.get('consent_confirmed')))
        self._loading = False
        self._count()
        text, kind = transcript_state(take) if take is not None else ('', None)
        set_chip(self.state, text, kind)
        self.copy_path.setEnabled(take is not None and take.export_path is not None)
        self.copy_text.setEnabled(take is not None and bool(value.strip()))
        self.suggest.setEnabled(take is not None and take.draft_request is None)


# ---- the Process page -------------------------------------------------------------------------------------------------

class StepCard(QFrame):
    """One processing step: a title, what it does, a switch, and the controls that belong to it."""

    def __init__(self, title, blurb, switch=True, parent=None):
        super().__init__(parent)
        self.setObjectName('card')
        layout = vbox(self, (14, 11, 14, 11), 6)
        head = hbox(spacing=8)
        head.addWidget(label(title, size=13, weight=QFont.Weight.DemiBold))
        head.addStretch(1)
        self.value = QLabel()
        self.value.setFont(theme.mono_font(10))
        self.value.setProperty('role', 'muted')
        head.addWidget(self.value)
        self.switch = Switch()
        self.switch.setVisible(switch)
        head.addWidget(self.switch)
        layout.addLayout(head)
        self.blurb = label(blurb, 'muted', wrap=True)
        layout.addWidget(self.blurb)
        self.body = vbox(spacing=6)
        layout.addLayout(self.body)

    def add_slider(self, low, high, scale=1):
        slider = QSlider(Qt.Orientation.Horizontal)
        slider.setRange(round(low * scale), round(high * scale))
        self.body.addWidget(slider)
        return slider

    def add_note(self, text, kind='amber'):
        note = chip(text, kind)
        note.setWordWrap(True)                              # a long message wraps inside the card instead of widening it
        note.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)
        self.body.addWidget(note)
        return note


def quiet(widget, set_value, value):
    """Set a control from the take without it telling anyone it changed."""
    widget.blockSignals(True)
    set_value(value)
    widget.blockSignals(False)


class ProcessPage(QWidget):
    """The processing chain: each step is a card bound to the take's recipe, and every change is one undo step."""

    def __init__(self, session, parent=None):
        super().__init__(parent)
        self.session = session
        layout = vbox(self, spacing=10)
        lo, hi = chain_mod.LIMITS[('highpass', 'hz')]
        self.hp = StepCard('High-pass filter', 'Removes room rumble and desk thumps below the cutoff.')
        self.hp_slider = self.hp.add_slider(lo, hi)
        self.hp.switch.toggled.connect(lambda on: self._set('highpass', f'High-pass {"on" if on else "off"}', enabled=on))
        self.hp_slider.valueChanged.connect(
            lambda v: self._set('highpass', f'High-pass {v} Hz', merge='highpass.hz', hz=float(v)))
        layout.addWidget(self.hp)

        self.hum = StepCard('Hum filter', 'Notches mains hum and its harmonics. The Check tab says if hum was found.')
        self.hum_region = Segmented(['50 Hz', '60 Hz'], 1)
        self.hum.body.addWidget(self.hum_region)
        self.hum.switch.toggled.connect(lambda on: self._set('hum', f'Hum filter {"on" if on else "off"}', enabled=on))
        self.hum_region.currentChanged.connect(
            lambda i: self._set('hum', f'Hum filter {(50, 60)[i]} Hz', hz=float((50, 60)[i])))
        layout.addWidget(self.hum)

        lo, hi = chain_mod.LIMITS[('denoise', 'nf')]
        self.denoise = StepCard('Noise reduction', 'Reduces steady background noise with FFmpeg (the afftdn filter). '
                                                   'Values nearer -20 dB clean more, and can change the voice.')
        self.denoise_slider = self.denoise.add_slider(lo, hi)
        self.denoise_note = self.denoise.add_note('Voice identity is not checked. Listen first.')
        self.denoise.switch.toggled.connect(self.session.editor.set_denoise)
        self.denoise_slider.valueChanged.connect(
            lambda v: self._set('denoise', f'Noise floor {v} dB', merge='denoise.nf', nf=float(v)))
        layout.addWidget(self.denoise)

        lo, hi = chain_mod.LIMITS[('level', 'gain_db')]
        self.level = StepCard('Level', f'Gain for the whole clip. Normalize puts the speech at {ac.TARGET_VOICED_DB:.0f} dB '
                                       'RMS.', switch=False)
        self.gain_slider = self.level.add_slider(lo, hi, 10)
        self.gain_slider.valueChanged.connect(
            lambda v: self._set('level', f'Gain {v / 10:+.1f} dB', merge='level.gain', gain_db=v / 10))
        row = hbox(spacing=6)
        self.normalize = button('Normalize', None, None, 'Set the gain so the speech sits at the target level')
        self.normalize.clicked.connect(lambda: self.session.editor.normalize_level())
        self.reset = button('Reset', 'ghost', None, 'Back to the recorded level')
        self.reset.clicked.connect(self.session.editor.reset_level)
        row.addWidget(self.normalize)
        row.addWidget(self.reset)
        row.addStretch(1)
        self.level.body.addLayout(row)
        layout.addWidget(self.level)

        lo, hi = chain_mod.LIMITS[('limiter', 'ceiling_db')]
        self.limiter = StepCard('Peak limiter', 'Catches a pop or a loud peak so Normalize can bring the speech up '
                                                'without clipping.')
        self.ceiling_slider = self.limiter.add_slider(lo, hi, 10)
        self.limiter.switch.toggled.connect(lambda on: self._set('limiter', f'Limiter {"on" if on else "off"}', enabled=on))
        self.ceiling_slider.valueChanged.connect(
            lambda v: self._set('limiter', f'Ceiling {v / 10:.1f} dB', merge='limiter.ceiling', ceiling_db=v / 10))
        layout.addWidget(self.limiter)

        self.fade = StepCard('Join fade', 'A short crossfade where two parts meet after a cut, so the join does not click.',
                             switch=False)
        self.fade_slider = self.fade.add_slider(0, ac.MAX_FADE_MS)
        self.fade_slider.valueChanged.connect(lambda v: self.session.editor.set_fade_ms(v))
        layout.addWidget(self.fade)

        layout.addWidget(label('Order:  cuts, silence and fades  →  filters  →  noise  →  level  →  limiter  →  export',
                               'dim', wrap=True))
        layout.addSpacing(2)
        layout.addWidget(caption('History'))
        self.history = QListWidget()
        self.history.setFixedHeight(150)
        self.history.itemClicked.connect(lambda item: self.session.editor.jump_to(self.history.row(item)))
        layout.addWidget(self.history)
        layout.addWidget(label('Click a step to go back to it. Ctrl+Z undoes, Ctrl+Y redoes.', 'dim', wrap=True))
        layout.addStretch(1)

    def _set(self, section, text, merge=None, **values):
        self.session.editor.set_chain(section, text, merge, **values)

    def refresh(self):
        take = self.session.current
        self.setEnabled(take is not None)
        if take is None:
            return
        history = take.history
        chain = history.chain
        hp, hum, nr, level, lim = chain['highpass'], chain['hum'], chain['denoise'], chain['level'], chain['limiter']
        quiet(self.hp.switch, self.hp.switch.setChecked, hp['enabled'])
        quiet(self.hp_slider, self.hp_slider.setValue, round(hp['hz']))
        self.hp_slider.setEnabled(hp['enabled'])
        self.hp.value.setText(f"{hp['hz']:.0f} Hz")
        quiet(self.hum.switch, self.hum.switch.setChecked, hum['enabled'])
        quiet(self.hum_region, self.hum_region.set_current, 0 if round(hum['hz']) == 50 else 1)
        self.hum_region.setEnabled(hum['enabled'])
        self.hum.value.setText(f"{hum['hz']:.0f} Hz")
        have_ffmpeg = clean.available()
        quiet(self.denoise.switch, self.denoise.switch.setChecked, nr['enabled'])
        quiet(self.denoise_slider, self.denoise_slider.setValue, round(nr['nf']))
        self.denoise_slider.setEnabled(nr['enabled'])
        self.denoise.switch.setEnabled(have_ffmpeg or nr['enabled'])
        self.denoise.value.setText(f"{nr['nf']:.0f} dB")
        set_chip(self.denoise_note, 'Voice identity is not checked. Listen first.' if have_ffmpeg else
                 'FFmpeg was not found. Install it or set its path in Settings.', 'amber')
        quiet(self.gain_slider, self.gain_slider.setValue, round(level['gain_db'] * 10))
        self.level.value.setText(f"{level['gain_db']:+.1f} dB")
        quiet(self.limiter.switch, self.limiter.switch.setChecked, lim['enabled'])
        quiet(self.ceiling_slider, self.ceiling_slider.setValue, round(lim['ceiling_db'] * 10))
        self.ceiling_slider.setEnabled(lim['enabled'])
        self.limiter.value.setText(f"{lim['ceiling_db']:.1f} dB")
        quiet(self.fade_slider, self.fade_slider.setValue, round(history.fade_ms))
        self.fade.value.setText(f'{history.fade_ms:.0f} ms')
        self._refresh_history(history)

    def _refresh_history(self, history):
        self.history.blockSignals(True)
        self.history.clear()
        for i, text in enumerate(['Start'] + history.steps):
            item = QListWidgetItem(text)
            if i > history.position:
                item.setForeground(color('dim'))
                item.setToolTip('Undone. Click to redo up to here.')
            self.history.addItem(item)
        self.history.setCurrentRow(history.position)
        self.history.scrollToItem(self.history.currentItem())
        self.history.blockSignals(False)


# ---- the Check page ---------------------------------------------------------------------------------------------------

STATUS_COLOURS = {'ok': 'green', 'warn': 'amber', 'bad': 'red', 'info': 'muted'}


class AutoCard(QFrame):
    """Auto analyze and Auto fix: what could be better about the clip, in plain words, and what fixing it would do.
    Once asked, it follows the clip as it is edited; after an Auto fix it also says what was done."""

    analyzeRequested = Signal()
    fixRequested = Signal()

    INTRO = ('Measures the clip and says in plain words what could be better. Auto fix then repairs what it safely can, '
             'as one step that Undo takes back. The recording itself is never changed.')

    def __init__(self, session, parent=None):
        super().__init__(parent)
        self.session = session
        self.setObjectName('card')
        self._signature = None
        layout = vbox(self, (14, 12, 14, 12), 8)
        head = hbox(spacing=8)
        head.addWidget(label('Auto analyze', size=13, weight=QFont.Weight.DemiBold))
        head.addStretch(1)
        self.verdict = chip('', 'gray')
        head.addWidget(self.verdict)
        layout.addLayout(head)
        row = hbox(spacing=8)                                   # above the findings, so the buttons never scroll away
        self.analyze = button('Auto analyze', None, 'analyze', 'Measure the clip and say what could be better')
        self.fix = button('Auto fix', 'primary', 'wand', 'Repair what Auto analyze finds, as one undo step')
        self.analyze.clicked.connect(self.analyzeRequested)
        self.fix.clicked.connect(self.fixRequested)
        row.addWidget(self.analyze)
        row.addWidget(self.fix)
        row.addStretch(1)
        layout.addLayout(row)
        self.body = vbox(spacing=8)
        layout.addLayout(self.body)

    # ---- the pieces ----

    @staticmethod
    def _finding(finding):
        box = vbox(spacing=2)
        head = hbox(spacing=8)
        dot = QLabel('●')
        dot.setStyleSheet(f'color: {PALETTE[STATUS_COLOURS[finding.status]]}; font-size: 12px;')
        head.addWidget(dot, 0, Qt.AlignmentFlag.AlignTop)
        head.addWidget(label(finding.title, size=13, weight=QFont.Weight.DemiBold), 1)
        box.addLayout(head)
        box.addWidget(label(finding.detail, 'muted', wrap=True))
        if finding.fix:
            fix = label(f'Auto fix: {finding.fix}', wrap=True)
            fix.setStyleSheet(f'color: {PALETTE["accent"]};')
            box.addWidget(fix)
        return box

    @staticmethod
    def _change(change):
        """One Check row that changes: its name and "before → after", on one line if they fit and on two if they do not."""
        text = f'{change.before}  →  {change.after}'
        short = len(text) <= 26
        value = QLabel(text)
        value.setFont(theme.mono_font(11))
        value.setWordWrap(not short)
        if not change.improved:
            value.setStyleSheet(f'color: {PALETTE["amber"]};')
        name = label(change.label, 'muted')
        if short:
            row = hbox(spacing=8)
            row.addWidget(name, 1)
            row.addWidget(value)
            return row
        box = vbox(spacing=1)
        box.addWidget(name)
        box.addWidget(value)
        return box

    def _changes(self, heading, changes):
        if not changes:
            return
        self.body.addWidget(caption(heading))
        for change in changes:
            self.body.addLayout(self._change(change))

    def _done(self, done):
        self.body.addWidget(caption('Auto fix did'))
        for step in done.steps:
            mark = label(f'✓  {step}', wrap=True)
            self.body.addWidget(mark)
        self._changes('Before and after', done.changes)

    # ---- drawing ----

    def refresh(self):
        session = self.session
        take, advisor = session.current, session.advisor
        advice, done = advisor.advice(take), advisor.done(take)
        watching = advisor.watching(take)
        signature = (take.id if take is not None else None, watching, advice.key if advice else None,
                     done.key if done else None)
        if signature == self._signature:
            return
        self._signature = signature
        clear_layout(self.body)
        diagnosis = advice.diagnosis if advice is not None else None
        kind, text = '', ''
        if not watching:
            self.body.addWidget(label(self.INTRO, 'muted', wrap=True))
        elif advice is None:
            kind, text = 'violet', 'Analyzing...'
            if done is not None:
                self._done(done)
        elif diagnosis is None:
            self.body.addWidget(label(f'Could not analyze the clip: {advice.error}', 'muted', wrap=True))
        else:
            if done is not None:
                self._done(done)
                self.body.addWidget(caption('What is left'))
            problems = [f for f in diagnosis.findings if f.status != 'ok']
            kind = 'green' if not problems else 'red' if any(f.status == 'bad' for f in problems) else 'amber'
            text = 'Nothing to fix' if not problems else f'{len(problems)} to look at'
            for finding in diagnosis.findings:
                self.body.addLayout(self._finding(finding))
            if diagnosis.has_fixes:
                self._changes('Auto fix will change', diagnosis.changes)
        set_chip(self.verdict, text, kind or None)
        self.setEnabled(take is not None)
        repairable = diagnosis is None or diagnosis.has_fixes
        self.fix.setEnabled(take is not None and repairable)
        self.fix.setToolTip('Repair what Auto analyze finds, as one undo step' if repairable
                            else 'There is nothing Auto fix can change')


class CheckPage(QWidget):
    """What was measured on the clip as Keep will write it, each with a verdict and what it means."""

    autoAnalyzeRequested = Signal()
    autoFixRequested = Signal()

    def __init__(self, session, parent=None):
        super().__init__(parent)
        self.session = session
        self._signature = None
        layout = vbox(self, spacing=8)
        self.auto = AutoCard(session)
        self.auto.analyzeRequested.connect(self.autoAnalyzeRequested)
        self.auto.fixRequested.connect(self.autoFixRequested)
        layout.addWidget(self.auto)
        layout.addWidget(label('Measured on the edited clip, exactly as Keep will write it.', 'muted', wrap=True))
        self.holder = QWidget()
        self.rows = vbox(self.holder, spacing=6)
        layout.addWidget(self.holder)
        layout.addStretch(1)

    def refresh(self, derived):
        signature = (derived.key if derived is not None else None, derived.error if derived is not None else None)
        if signature == self._signature:
            return
        self._signature = signature
        clear_layout(self.rows)
        if derived is None:
            self.rows.addWidget(label('Measuring...', 'dim'))
        elif derived.report is None:
            self.rows.addWidget(label(f'Could not measure the clip: {derived.error}', 'muted', wrap=True))
        else:
            for row in derived.report:
                self.rows.addWidget(self._row(row))

    @staticmethod
    def _row(row):
        box = card()
        layout = vbox(box, (12, 9, 12, 9), 3)
        head = hbox(spacing=8)
        dot = QLabel('●')
        dot.setStyleSheet(f'color: {PALETTE[STATUS_COLOURS[row.status]]}; font-size: 12px;')
        head.addWidget(dot)
        head.addWidget(label(row.label, size=13, weight=QFont.Weight.DemiBold))
        head.addStretch(1)
        value = QLabel(row.value)
        value.setFont(theme.mono_font(11))
        head.addWidget(value)
        layout.addLayout(head)
        layout.addWidget(label(row.hint, 'dim', size=11, wrap=True))
        return box


# ---- the Keep checklist -----------------------------------------------------------------------------------------------

GLYPHS = {'ok': ('✓', 'green'), 'todo': ('○', 'dim'), 'bad': ('✕', 'red'), 'advice': ('!', 'amber')}


def step_text(step):
    """The checklist line for a rule: what is met, or what is still to do."""
    key, detail = step.key, step.detail
    if key == 'length':
        if detail == 'measuring...':
            return 'Measuring the clip...'
        return f'Length {detail}  (6 to 40 s)' if step.done else f'Length {detail}: a reference must be 6 to 40 s'
    if key == 'audible':
        return 'Not silent' if step.done else ('Measuring the clip...' if detail == 'measuring...'
                                               else f'Silent ({detail}): there is nothing to keep')
    if key == 'transcript':
        return f'Transcript, {detail}' if step.done else \
            f'Transcript must be {ac.TRANSCRIPT_MIN} to {ac.TRANSCRIPT_MAX} characters ({detail})'
    if key == 'checked':
        return 'Transcript matches the kept words' if step.done else 'Check that the transcript still matches the words kept'
    if key == 'origin':
        return 'Origin entered' if step.done else 'Enter the origin: where the recording came from'
    if key == 'allowed_use':
        return 'Allowed use entered' if step.done else 'Enter the allowed use'
    return 'Permission confirmed' if step.done else 'Tick the permission box'


class ReadyCard(QFrame):
    """"Ready to keep?": what is still to do, advice that does not block, and the Keep, Re-record and Discard buttons.
    Always visible, so nothing is learned from a dialog after pressing Keep. The checks that already pass are folded
    into one line that opens the full list."""

    keepRequested = Signal()
    rerecordRequested = Signal()
    discardRequested = Signal()
    folderRequested = Signal()
    confirmRequested = Signal()

    def __init__(self, session, parent=None):
        super().__init__(parent)
        self.session = session
        self.setObjectName('card')
        self.expanded = False
        self._signature = None
        self._derived = None
        layout = vbox(self, (16, 13, 16, 12), 7)
        head = hbox(spacing=8)
        head.addWidget(label('Ready to keep?', size=14, weight=QFont.Weight.DemiBold))
        head.addStretch(1)
        self.verdict = chip('', 'red')
        head.addWidget(self.verdict)
        layout.addLayout(head)
        self.rows = vbox(spacing=6)
        layout.addLayout(self.rows)
        layout.addSpacing(4)
        self.keep = button('Keep clip     Ctrl+S', 'primary')
        self.keep.clicked.connect(self.keepRequested)
        layout.addWidget(self.keep)
        more = hbox(spacing=4)
        self.rerecord = button('Re-record', 'ghost', None, 'Move this take to the Recycle Bin and record again')
        self.discard = button('Discard', 'danger', None, 'Move this take to the Recycle Bin')
        self.open_folder = button('Open folder', 'ghost', None, 'Show the speaker folder in Explorer')
        self.rerecord.clicked.connect(self.rerecordRequested)
        self.discard.clicked.connect(self.discardRequested)
        self.open_folder.clicked.connect(self.folderRequested)
        for b in (self.rerecord, self.discard, self.open_folder):
            more.addWidget(b)
        layout.addLayout(more)

    def _line(self, state, text, action=None):
        row = hbox(spacing=9)
        glyph, name = GLYPHS[state]
        mark = QLabel(glyph)
        mark.setFont(theme.ui_font(13, QFont.Weight.Bold))
        mark.setFixedWidth(16)
        mark.setStyleSheet(f'color: {PALETTE[name]};')
        row.addWidget(mark, 0, Qt.AlignmentFlag.AlignTop)
        words = label(text, 'muted' if state in ('ok', 'todo') else None, wrap=True)
        row.addWidget(words, 1)
        if action is not None:
            row.addWidget(action, 0, Qt.AlignmentFlag.AlignTop)
        return row

    def _toggle(self):
        self.expanded = not self.expanded
        self._signature = None
        self.refresh(self._derived)

    def _passed_line(self, count):
        """One line standing in for the checks that pass, with a link that shows or hides them."""
        row = hbox(spacing=9)
        mark = QLabel(GLYPHS['ok'][0])
        mark.setFont(theme.ui_font(13, QFont.Weight.Bold))
        mark.setFixedWidth(16)
        mark.setStyleSheet(f'color: {PALETTE["green"]};')
        row.addWidget(mark)
        row.addWidget(label(f'{count} check{"s" if count != 1 else ""} passed', 'muted'))
        toggle = button('Hide' if self.expanded else 'Show', 'link')
        toggle.clicked.connect(self._toggle)
        row.addWidget(toggle)
        row.addStretch(1)
        return row

    def refresh(self, derived):
        take = self.session.current
        self.setEnabled(take is not None)
        self._derived = derived
        readiness = self.session.workspace.readiness(derived)
        signature = (take.id if take else None, take.kept if take else None, self.expanded,
                     tuple(readiness.steps), tuple(readiness.advice), readiness.ready)
        if signature == self._signature:
            return
        self._signature = signature
        clear_layout(self.rows)
        passed = [step for step in readiness.steps if step.done]
        shown, said = (readiness.steps if self.expanded else [s for s in readiness.steps if not s.done]), set()
        for step in shown:
            state = 'ok' if step.done else 'bad' if step.key in ('length', 'audible') and step.detail != 'measuring...' \
                else 'todo'
            text = step_text(step)
            if text in said:                                    # two checks waiting on the same measurement say it once
                continue
            said.add(text)
            action = None
            if step.key == 'checked' and not step.done:
                action = button('It matches', 'link')
                action.clicked.connect(self.confirmRequested)
            self.rows.addLayout(self._line(state, text, action))
        if passed:
            self.rows.addLayout(self._passed_line(len(passed)))
        for advice in readiness.advice:
            self.rows.addLayout(self._line('advice', advice.text))
        blockers = sum(1 for step in readiness.steps if not step.done)
        set_chip(self.verdict, 'Ready' if readiness.ready else f'{blockers} to fix', 'green' if readiness.ready else 'red')
        self.keep.setText('Update kept clip     Ctrl+S' if take is not None and take.kept else 'Keep clip     Ctrl+S')
        self.keep.setEnabled(readiness.ready)
        self.keep.setToolTip('Writes the 24 kHz WAV and its sidecar' if readiness.ready else 'Fix the items above first')
        self.discard.setEnabled(take is not None)
        self.rerecord.setEnabled(take is not None)


# ---- while recording --------------------------------------------------------------------------------------------------

class RecordCard(QFrame):
    """Shown instead of the tabs while a take is being made: where it comes from and where it is saved."""

    def __init__(self, session, parent=None):
        super().__init__(parent)
        self.session = session
        self.setObjectName('card')
        layout = vbox(self, (16, 14, 16, 14), 8)
        layout.addWidget(caption('This take'))
        self.values = {}
        for key, name in (('device', 'Device'), ('capture', 'Capture'), ('pre_roll', 'Pre-roll'), ('tail', 'Tail'),
                          ('saved', 'Saved to')):
            row = hbox(spacing=10)
            title = label(name, 'dim')
            title.setFixedWidth(70)
            value = label('', wrap=True)
            row.addWidget(title, 0, Qt.AlignmentFlag.AlignTop)
            row.addWidget(value, 1)
            layout.addLayout(row)
            self.values[key] = value
        layout.addSpacing(6)
        layout.addWidget(label('Editing opens when you stop. The original is written to disk first, so Stop never loses a '
                               'take.', 'muted', wrap=True))

    def refresh(self):
        info = self.session.capturing.info()
        workspace = self.session.workspace
        rate = f"{info['rate'] / 1000:g} kHz" if info['rate'] else 'opens when recording starts'
        channels = f"  ·  {info['channels']} ch" if info['channels'] else ''
        self.values['device'].setText(info['device'] or 'No device')
        self.values['capture'].setText(rate + channels)
        self.values['pre_roll'].setText(f"{info['pre_roll']:g} s kept before Record" if info['pre_roll']
                                        else 'none for system audio')
        self.values['tail'].setText(f"{info['tail']:g} s after Stop")
        self.values['saved'].setText(f"{workspace.name_of(workspace.speaker) or 'My voice'}\\masters\\ at Stop")


# ---- the panel --------------------------------------------------------------------------------------------------------

def scrolling(page):
    """A page in its own scroll area, so it scrolls by its own height and never takes the height of a taller page."""
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QFrame.Shape.NoFrame)
    scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    holder = QWidget()
    inner = vbox(holder, (0, 0, 8, 0))
    inner.addWidget(page)
    inner.addStretch(1)
    scroll.setWidget(holder)
    return scroll


class Inspector(QWidget):
    """Clip, Process and Check tabs over the Keep checklist; a plain "This take" card while recording; a short note when
    no take is open."""

    keepRequested = Signal()
    rerecordRequested = Signal()
    discardRequested = Signal()
    folderRequested = Signal()
    suggestRequested = Signal()
    copyRequested = Signal(str)
    confirmRequested = Signal()
    autoAnalyzeRequested = Signal()
    autoFixRequested = Signal()

    REVIEW, RECORDING, IDLE = 0, 1, 2
    CLIP_TAB, PROCESS_TAB, CHECK_TAB = 0, 1, 2

    def __init__(self, session, parent=None):
        super().__init__(parent)
        self.session = session
        self.setObjectName('inspector')
        self.setFixedWidth(372)
        self._mode = self.REVIEW
        outer = vbox(self)
        self.modes = QStackedWidget()
        outer.addWidget(self.modes)

        review = QWidget()
        layout = vbox(review, (16, 14, 16, 14), 12)
        self.tabs = Segmented(['Clip', 'Process', 'Check'])
        layout.addWidget(self.tabs)
        self.clip = ClipPage(session)
        self.process = ProcessPage(session)
        self.check = CheckPage(session)
        self.pages = QStackedWidget()
        for page in (self.clip, self.process, self.check):
            self.pages.addWidget(scrolling(page))
        self.tabs.currentChanged.connect(self.pages.setCurrentIndex)
        layout.addWidget(self.pages, 1)
        self.ready = ReadyCard(session)
        layout.addWidget(self.ready)
        self.modes.addWidget(review)

        recording = QWidget()
        rec_layout = vbox(recording, (16, 14, 16, 14), 12)
        rec_layout.addWidget(self._disabled_tabs())
        self.record_card = RecordCard(session)
        rec_layout.addWidget(self.record_card)
        rec_layout.addStretch(1)
        self.modes.addWidget(recording)

        idle = QWidget()
        idle_layout = vbox(idle, (28, 28, 28, 28), 10)
        idle_layout.addStretch(2)
        title = label('No clip open', size=15, weight=QFont.Weight.DemiBold)
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        idle_layout.addWidget(title)
        note = label('Open a take and its transcript, origin and allowed use are written here. Then process it, check the '
                     'measurements and keep it as a clip.', 'muted', wrap=True)
        note.setAlignment(Qt.AlignmentFlag.AlignCenter)
        idle_layout.addWidget(note)
        idle_layout.addStretch(3)
        self.modes.addWidget(idle)

        self.clip.suggestRequested.connect(self.suggestRequested)
        self.clip.copyRequested.connect(self.copyRequested)
        self.check.autoAnalyzeRequested.connect(self.autoAnalyzeRequested)
        self.check.autoFixRequested.connect(self.autoFixRequested)
        for signal, forward in ((self.ready.keepRequested, self.keepRequested),
                                (self.ready.rerecordRequested, self.rerecordRequested),
                                (self.ready.discardRequested, self.discardRequested),
                                (self.ready.folderRequested, self.folderRequested),
                                (self.ready.confirmRequested, self.confirmRequested)):
            signal.connect(forward)

    def _disabled_tabs(self):
        tabs = Segmented(['Clip', 'Process', 'Check'])
        tabs.setEnabled(False)
        return tabs

    def set_mode(self, mode):
        """REVIEW while a take is open, RECORDING while one is being made, IDLE when there is nothing to show."""
        if mode == self.RECORDING and self._mode != self.RECORDING:
            self.record_card.refresh()
        self._mode = mode
        self.modes.setCurrentIndex(mode)

    def show_tab(self, index):
        self.tabs.set_current(index)
        self.pages.setCurrentIndex(index)

    def focus_label(self):
        """Show the Clip tab with the cursor in the Label field, ready to give the take a name."""
        self.show_tab(self.CLIP_TAB)
        self.clip.name_edit.setFocus()
        self.clip.name_edit.selectAll()

    def refresh_meta(self, derived):
        self.clip.refresh()
        self.ready.refresh(derived)

    def refresh_edits(self):
        self.process.refresh()
        self.clip.refresh()
        self.refresh_advice()

    def refresh_analysis(self, derived):
        self.check.refresh(derived)
        self.ready.refresh(derived)

    def refresh_advice(self):
        self.check.auto.refresh()
