"""The lists of microphones and outputs: each device once, by its full name.

Windows offers every device through four audio interfaces (MME, DirectSound, WASAPI and WDM-KS), and the sound library
lists each of them, so one microphone came up four times, and MME cuts names at 31 letters. By default only WASAPI's
entries are listed, which is one for each device with its whole name, at the rate the device runs at. A setting
(`all_interfaces`) lists every interface for the rare PC that needs it, and when WASAPI offers nothing of a kind, as in a
remote desktop session, every interface is listed without asking.

A choice is remembered by the name shown. A name kept by 0.6 had its interface after it ("Microphone  [MME]"), possibly
cut at 31 letters; `match` finds the same device in the list shown now, and falls back to nothing rather than guess.

The sound library is imported only when asked for, so the rest runs without audio hardware.
"""
import re

WASAPI = 'Windows WASAPI'
MME_LETTERS = 31                    # MME cuts a device's name at this many letters
_LABELLED = re.compile(r'(.*?)  \[([^\]]+)\]')


def label(name, api):
    """How a device is named when every interface is listed: its name, two spaces, and the interface in brackets."""
    return f'{name}  [{api}]'


def entries(kind, every=False):
    """[(index, name shown)] for devices with `kind` ('input' or 'output') channels: each once, as WASAPI names them,
    or with `every`, each of them through every interface, with the interface after the name."""
    import sounddevice as sd
    apis = sd.query_hostapis()
    found = [(index, d['name'], apis[d['hostapi']]['name']) for index, d in enumerate(sd.query_devices())
             if d[f'max_{kind}_channels'] > 0]
    once = [(index, name) for index, name, api in found if api == WASAPI]
    if once and not every:
        return once
    return [(index, label(name, api)) for index, name, api in found]


def default(kind):
    """Windows' own default device of `kind`: WASAPI's, which is the one Windows' Sound settings name; else the sound
    library's default, else the first interface that has one. None when there is none."""
    import sounddevice as sd
    apis = list(sd.query_hostapis())
    try:
        own = sd.default.device[0 if kind == 'input' else 1]
    except (TypeError, IndexError):
        own = -1
    wasapi = [api[f'default_{kind}_device'] for api in apis if api['name'] == WASAPI]
    others = [api[f'default_{kind}_device'] for api in apis if api['name'] != WASAPI]
    for index in (*wasapi, own, *others):
        if isinstance(index, int) and index >= 0:
            return index
    return None


def split(shown):
    """(name, interface) of a name as shown; the interface is None for a name shown alone."""
    found = _LABELLED.fullmatch(shown)
    return (found[1], found[2]) if found else (shown, None)


def match(remembered, listed):
    """The index of the entry in `listed` [(index, name shown)] that `remembered` names, or None.

    The same name shown is the answer. Otherwise the device's own name is looked for without its interface: through the
    same interface if it is listed, else through WASAPI, else the first. A name MME cut at 31 letters, or cut in the list
    now, counts only when it fits one device and no other."""
    if not remembered:
        return None
    for index, shown in listed:
        if shown == remembered:
            return index
    name, api = split(remembered)
    named = [(index, *split(shown)) for index, shown in listed]
    same = [(index, their_api) for index, their_name, their_api in named if their_name == name]
    if same:
        for wanted in (api, WASAPI):
            for index, their_api in same:
                if wanted is not None and their_api == wanted:
                    return index
        return same[0][0]
    cut = {their_name for _, their_name, _ in named
           if (len(name) == MME_LETTERS and their_name.startswith(name))
           or (len(their_name) == MME_LETTERS and name.startswith(their_name))}
    if len(cut) != 1:
        return None
    return match(cut.pop(), listed)
