"""Measurements of the current recipe, computed in the background and remembered per take.

`derived(take)` returns the measurement of the take as it is now, or None while it is being computed. The result
holds the export's statistics (what the keep rules judge), the rows of the Check tab and the processed clip's length.
The spectrogram is the master's, so it does not change with the recipe.
"""
import copy
import json
from collections import namedtuple
from types import SimpleNamespace

from .. import audio_core as ac
from ..core import analysis

Derived = namedtuple('Derived', 'key stats report error')


def frozen(history):
    """A copy of the recipe a background job can use while the window goes on editing the original."""
    return SimpleNamespace(edits=copy.deepcopy(history.edits), chain=copy.deepcopy(history.chain),
                           fade_ms=history.fade_ms)


def recipe_key(history):
    return json.dumps([history.edits, history.chain, history.fade_ms], sort_keys=True)


class Measurements:
    def __init__(self, bus, workspace, jobs):
        self.bus = bus
        self.workspace = workspace
        self.jobs = jobs

    def derived(self, take):
        if take is None:
            return None
        key = recipe_key(take.history)
        have = take.cache.get('derived')
        if have is not None and have.key == key:
            return have
        if take.cache.get('derived_requested') != key:
            take.cache['derived_requested'] = key
            recipe, lib = frozen(take.history), self.workspace.lib

            def work():
                export = lib.export_for(take, recipe)
                return Derived(key, ac.measure(export, ac.EXPORT_RATE), analysis.quality_report(export), '')

            def done(result):
                if take.cache.get('derived_requested') == key:      # a result for a recipe since changed is dropped
                    take.cache['derived'] = result
                    self.bus.notify('analysis')

            def failed(error):
                if take.cache.get('derived_requested') == key:
                    take.cache['derived'] = Derived(key, None, None, str(error))
                    self.bus.notify('analysis')

            self.jobs.submit(work, done, failed)
        return None

    def spectrogram(self, take):
        """The master's spectrogram once it has been computed, else None (a request is made)."""
        if take is None:
            return None
        if 'spectrogram' in take.cache:
            return take.cache['spectrogram']
        if not take.cache.get('spectrogram_requested'):
            take.cache['spectrogram_requested'] = True

            def done(result):
                take.cache['spectrogram'] = result
                self.bus.notify('analysis')

            self.jobs.submit(lambda: analysis.spectrogram(take.mono, take.rate), done,
                             lambda error: take.cache.update(spectrogram_requested=False))
        return None

    def pyramid(self, take):
        """The master's peak pyramid for drawing, built once per take."""
        if 'pyramid' not in take.cache:
            take.cache['pyramid'] = analysis.PeakPyramid(take.mono)
        return take.cache['pyramid']
