## What does this change, and why?

## How did you check it?

<!-- For a microphone, system-audio or playback change, say what you tried by hand and on which hardware. -->

## Checklist

- [ ] `python -m pytest` passes
- [ ] `python -m pyflakes recorder tests tools` reports nothing
- [ ] New or changed behaviour has tests (a bug fix has a test that fails without the fix)
- [ ] The documentation and the *Unreleased* part of `CHANGELOG.md` are updated
- [ ] If the window changed, the screenshots in `docs/images` are redrawn with `python tools/shoot.py`
- [ ] No recordings, `settings.json`, or personal paths or names are in this change
- [ ] This does not weaken the consent step, add network access, or add tracking
