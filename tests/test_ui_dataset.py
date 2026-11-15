"""The Export dataset dialog's layouts and split, and a dataset exported from the window with a split."""
import pytest

pytest.importorskip('PySide6')

from helpers import fill, finish_batch, record  # noqa: E402
from recorder.ui.dialogs import DatasetDialog  # noqa: E402


def test_the_dialog_offers_every_layout_at_the_rate_its_tool_expects(qapp):
    shown = DatasetDialog('Ann Lee')
    layouts = [shown.layout_choice.itemData(n) for n in range(shown.layout_choice.count())]
    assert layouts == ['clips', 'ljspeech', 'audiofolder', 'kaldi']
    for layout, rate in (('kaldi', 16000), ('audiofolder', 24000), ('ljspeech', 22050), ('clips', 24000)):
        shown.layout_choice.setCurrentIndex(shown.layout_choice.findData(layout))
        assert shown.rate.currentData() == rate
    shown.close()


def test_the_dialog_starts_with_no_split_and_offers_one(qapp):
    shown = DatasetDialog('Ann Lee')
    assert shown.split.currentData() == 0 and shown.values()[-1] == 0
    assert [shown.split.itemData(n) for n in range(shown.split.count())] == [0, 5, 10, 20]
    shown.split.setCurrentIndex(shown.split.findData(10))
    assert shown.values() == ('speaker', 'clips', 24000, True, 10)
    assert shown.split.currentText() == '90% for training, 10% for validation'
    shown.close()


def test_the_window_exports_a_split_dataset(window, session, dialogs, tmp_path):
    for n in range(4):
        record(session, 8.0)
        fill(session, transcript=f'Line {n + 1}.')
        session.keep()
    window.flush()
    dialogs.dataset_answer = ('speaker', 'audiofolder', 24000, False, 20)
    dialogs.folder_answer = str(tmp_path / 'set')
    window.export_dataset()
    finish_batch(session)
    window.flush()
    assert len(list((tmp_path / 'set' / 'train').glob('*.wav'))) == 3
    assert len(list((tmp_path / 'set' / 'validation').glob('*.wav'))) == 1
    assert session.status_text.endswith(': 3 for training and 1 for validation')
