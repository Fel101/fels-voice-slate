"""Check the shared Fel's Studio contract with Voice Lab's test vectors."""
from pathlib import Path

from recorder import fels_suite


def test_shared_suite_vectors(tmp_path, monkeypatch):
    monkeypatch.setenv('FELS_SUITE_DIR', str(tmp_path))
    failures = fels_suite.check_vectors(Path(__file__).with_name('suite_vectors.json'))
    assert not failures, '\n'.join(failures)
