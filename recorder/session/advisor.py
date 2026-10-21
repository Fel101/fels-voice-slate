"""Auto analyze and Auto fix for the current take.

The advice is worked out in the background for the recipe the take has now (the audio itself is never touched) and
remembered until the recipe changes. Once someone has asked, the take is watched: later edits bring fresh advice, so the
Check tab always describes the clip as it is. Auto fix applies the plan as one undo step, as soon as it is known.
"""
from collections import namedtuple

from .. import clean
from ..core import autofix
from .measure import recipe_key

Advice = namedtuple('Advice', 'key diagnosis error')            # diagnosis is None when the clip could not be looked at
Done = namedtuple('Done', 'key steps changes')                  # what the last Auto fix did, for the recipe it produced


class Advisor:
    def __init__(self, bus, workspace, editor, jobs):
        self.bus = bus
        self.workspace = workspace
        self.editor = editor
        self.jobs = jobs

    # ---- what the window asks -----------------------------------------------------------------------------------------

    @staticmethod
    def watching(take):
        return take is not None and bool(take.cache.get('advice_watched'))

    @staticmethod
    def advice(take):
        """The advice for the take as it is now, or None (not asked for, still being worked out, or out of date)."""
        have = take.cache.get('advice') if take is not None else None
        return have if have is not None and have.key == recipe_key(take.history) else None

    def working(self, take):
        """True while the advice for the take as it is now is being worked out."""
        return self.watching(take) and self.advice(take) is None

    @staticmethod
    def done(take):
        """What Auto fix did to produce the take as it is now, or None if anything was changed since."""
        have = take.cache.get('advice_done') if take is not None else None
        return have if have is not None and have.key == recipe_key(take.history) else None

    @staticmethod
    def summary(advice):
        """One line for the status bar: how much there is to look at and whether Auto fix has anything ready."""
        diagnosis = advice.diagnosis
        if diagnosis is None:
            return f'Could not analyze the clip: {advice.error}'
        problems = [f for f in diagnosis.findings if f.status != 'ok']
        if not problems:
            return 'Auto analyze: nothing to fix. The Check tab has the measurements.'
        text = f'Auto analyze: {len(problems)} thing{"" if len(problems) == 1 else "s"} to look at.'
        if diagnosis.has_fixes:
            steps = len(diagnosis.steps)
            return f'{text} Auto fix has {steps} step{"" if steps == 1 else "s"} ready.'
        return f'{text} Auto fix has nothing it can repair.'

    # ---- the two commands ---------------------------------------------------------------------------------------------

    def analyze(self):
        """Auto analyze: look at the current take, and keep the advice up to date as it is edited. False if no take."""
        take = self.workspace.current
        if take is None:
            return False
        take.cache['advice_watched'] = True
        have = self.advice(take)
        if have is not None:
            self.bus.status(self.summary(have))
        else:
            take.cache['advice_announce'] = recipe_key(take.history)
            self.bus.status('Analyzing the clip...')
            self.refresh()
        self.bus.notify('advice')
        return True

    def fix(self):
        """Auto fix: repair the current take as Auto analyze plans. If the plan is not known yet it is applied the moment
        it is. Returns True if it was applied, or is on its way."""
        take = self.workspace.current
        if take is None:
            return False
        take.cache['advice_watched'] = True
        have = self.advice(take)
        if have is not None:
            return self._apply(take, have)
        take.cache['advice_fix'] = recipe_key(take.history)
        self.bus.status('Auto fix: looking at the clip first...')
        self.refresh()
        self.bus.notify('advice')
        return True

    def refresh(self):
        """Start working out the advice for the current recipe if the take is watched and nobody has yet."""
        take = self.workspace.current
        if not self.watching(take):
            return
        key = recipe_key(take.history)
        if self.advice(take) is None and take.cache.get('advice_requested') != key:
            self._request(take, key)

    # ---- the work -----------------------------------------------------------------------------------------------------

    def _request(self, take, key):
        take.cache['advice_requested'] = key
        history, lib, can_clean = take.history.copy(), self.workspace.lib, clean.available()

        def work():
            diagnosis = autofix.diagnose(take.samples, take.rate, history, lambda recipe: lib.before_level(take, recipe),
                                         denoise_ok=can_clean)
            return Advice(key, diagnosis, '')

        def arrived(result):
            if take.cache.get('advice_requested') == key:           # a result for a recipe since changed is dropped
                take.cache['advice'] = result
                self.bus.notify('advice')
                if take.cache.get('advice_announce') == key:
                    take.cache.pop('advice_announce')
                    self.bus.status(self.summary(result))
                self._fix_if_asked(take, result)

        def failed(error):
            arrived(Advice(key, None, str(error)))

        self.jobs.submit(work, arrived, failed)

    def _fix_if_asked(self, take, advice):
        """Auto fix was pressed before the plan was known: apply it now, unless the clip has been changed since."""
        asked = take.cache.pop('advice_fix', None)
        if asked is None or take is not self.workspace.current:
            return
        if asked != advice.key or advice.key != recipe_key(take.history):
            self.bus.status('The clip changed while Auto fix was looking at it. Press Auto fix again.')
            return
        self._apply(take, advice)

    def _apply(self, take, advice):
        if advice.diagnosis is None:
            self.bus.status(f'Auto fix could not look at the clip: {advice.error}')
            return False
        diagnosis = advice.diagnosis
        if not diagnosis.has_fixes:
            self.bus.status('Auto fix: nothing here it can repair. The Check tab says what is left.')
            self.bus.notify('advice')
            return False
        if not self.editor.apply_auto_fix(diagnosis):
            return False
        take.cache['advice_done'] = Done(recipe_key(take.history), list(diagnosis.steps), diagnosis.changes)
        self.refresh()
        self.bus.notify('advice')
        return True
