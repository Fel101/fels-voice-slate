"""Script mode (the prompts a speaker reads) and the bus the window listens to."""
import pytest

from recorder.session import script as script_mod
from recorder.session.bus import Bus
from recorder.session.script import Script


@pytest.fixture
def script():
    return Script(Bus())


def test_a_script_is_split_into_lines_and_starts_at_the_first(script):
    script.load_text('First line to read aloud.\n\nSecond line to read aloud.\n')
    assert script.lines == ['First line to read aloud.', 'Second line to read aloud.']
    assert script.current == 'First line to read aloud.' and script.label == '1 of 2'


def test_stepping_stays_inside_the_script(script):
    script.load_text('One one one.\nTwo two two.')
    script.step(5)
    assert script.index == 1 and script.label == '2 of 2'
    script.step(-9)
    assert script.index == 0


def test_with_no_script_there_is_no_current_line(script):
    assert script.current is None and script.label == ''
    script.step(1)
    assert script.index == 0


def test_clearing_the_script_forgets_the_lines(script):
    script.load_text('One one one.')
    script.clear()
    assert script.lines == [] and script.current is None


def test_a_script_file_is_read_as_utf8(script, tmp_path):
    path = tmp_path / 'lines.txt'
    path.write_text('Café au lait, s\'il vous plaît.\n', encoding='utf-8')
    assert script.load_file(path) is True
    assert script.current == "Café au lait, s'il vous plaît."


@pytest.mark.parametrize('encoding', ['utf-8-sig', 'utf-16', 'utf-16-le-bom', 'utf-16-be-bom'])
def test_what_notepad_saves_is_read_without_a_stray_mark_in_the_first_line(script, tmp_path, encoding):
    path = tmp_path / 'lines.txt'
    text = 'Café au lait.\nSecond line.\n'
    if encoding.endswith('-bom'):
        name = encoding[:-4]
        path.write_bytes(('\ufeff' + text).encode(name))
    else:
        path.write_text(text, encoding=encoding)
    assert script.load_file(path) is True
    assert script.lines == ['Café au lait.', 'Second line.']


def test_a_file_in_the_windows_code_page_is_read(script, tmp_path, monkeypatch):
    monkeypatch.setattr(script_mod.locale, 'getencoding', lambda: 'cp1252')
    path = tmp_path / 'lines.txt'
    path.write_bytes('Café and “quotes”.\n'.encode('cp1252'))
    assert script.load_file(path) is True and script.current == 'Café and “quotes”.'


@pytest.mark.parametrize('raw', [b'\x00\x01\x02 not text', 'Words\x00in\x00UTF-16 with no mark'.encode('utf-16-le')])
def test_a_file_that_is_not_text_is_refused_with_a_notice(script, tmp_path, raw):
    path = tmp_path / 'lines.bin'
    path.write_bytes(raw)
    assert script.load_file(path) is False
    assert 'not plain text' in script.bus.notices[-1].text and script.lines == []


def test_a_missing_script_file_is_refused_with_a_notice(script, tmp_path):
    assert script.load_file(tmp_path / 'nothing.txt') is False
    assert script.bus.notices[-1].level == 'error'


def test_loading_a_script_tells_the_listeners():
    bus, heard = Bus(), []
    bus.listen(lambda topic, payload: heard.append(topic))
    Script(bus).load_text('One one one.')
    assert heard == ['script']


def test_the_bus_keeps_the_latest_status_and_the_last_fifty_notices():
    bus = Bus()
    bus.status('Hello.')
    for number in range(60):
        bus.notice('info', 'T', str(number))
    assert bus.status_text == 'Hello.' and len(bus.notices) == 50 and bus.notices[-1].text == '59'


def test_a_listener_that_adds_another_listener_does_not_break_the_notification():
    bus, heard = Bus(), []
    bus.listen(lambda topic, payload: bus.listen(lambda t, p: heard.append(t)))
    bus.notify('first')
    bus.notify('second')
    assert heard == ['second']
