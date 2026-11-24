"""The release kit: SHA256SUMS, the check of a draft release's files, the notes, and the release workflow's order."""
import hashlib
import re
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'tools'))
import check_release_files as kit  # noqa: E402
import release_notes  # noqa: E402

TAG = 'v1.2.3'
INSTALLER = 'Fels-Voice-Slate-1.2.3-Setup.exe'
WORKFLOW = ROOT / '.github' / 'workflows' / 'release.yml'


@pytest.fixture
def draft(tmp_path):
    """A folder as the release workflow downloads it from the draft: the installer and its SHA256SUMS."""
    installer = tmp_path / INSTALLER
    installer.write_bytes(b'MZ' + bytes(range(256)) * 300)
    kit.write_sums(installer)
    return tmp_path


def test_sums_hold_one_line_that_sha256sum_reads(draft):
    digest = hashlib.sha256((draft / INSTALLER).read_bytes()).hexdigest()
    assert (draft / kit.SUMS).read_bytes() == f'{digest}  {INSTALLER}\n'.encode('ascii')
    assert kit.check_files(draft, TAG) == [draft / INSTALLER, draft / kit.SUMS]


def test_the_installer_build_writes_the_sums():
    import build_installer

    assert build_installer.write_sums is kit.write_sums
    assert 'write_sums(made)' in (ROOT / 'tools' / 'build_installer.py').read_text(encoding='utf-8')


@pytest.mark.parametrize('change, problem', [
    (lambda folder: (folder / kit.SUMS).unlink(), 'missing SHA256SUMS'),
    (lambda folder: (folder / 'notes.txt').write_text('x'), 'not expected notes.txt'),
    (lambda folder: (folder / INSTALLER).rename(folder / 'Fels-Voice-Slate-1.2.2-Setup.exe'),
     f'missing {INSTALLER}; not expected Fels-Voice-Slate-1.2.2-Setup.exe'),
    (lambda folder: (folder / INSTALLER).write_bytes(b'changed'), 'changed after SHA256SUMS was written'),
    (lambda folder: (folder / kit.SUMS).write_text((folder / kit.SUMS).read_text() * 2), 'it holds 2'),
    (lambda folder: (folder / kit.SUMS).write_text('0' * 64 + '  other.exe\n'), 'should hold one line'),
    (lambda folder: (folder / kit.SUMS).write_bytes(b'\xff\xfe'), 'it holds 0'),
])
def test_anything_but_the_built_files_is_refused(draft, change, problem):
    change(draft)
    with pytest.raises(kit.ReleaseFilesError, match=re.escape(problem)):
        kit.check_files(draft, TAG)


@pytest.mark.parametrize('tag', ['1.2.3', 'v1.2', 'v1.2.3-rc1', '', None])
def test_only_a_release_tag_names_the_files(tag):
    with pytest.raises(kit.ReleaseFilesError, match='not a release tag'):
        kit.installer_name(tag)


def test_each_file_must_be_attested_by_this_repositorys_release_workflow(draft):
    asked = []

    def run(command):
        asked.append(command)
        return SimpleNamespace(returncode=0)

    paths = kit.check_files(draft, TAG)
    kit.check_attestations(paths, kit.REPOSITORY, run=run, which=lambda name: 'gh')
    assert asked == [['gh', 'attestation', 'verify', str(path), '--repo', 'Fel101/fels-voice-slate', '--signer-workflow',
                      'Fel101/fels-voice-slate/.github/workflows/release.yml'] for path in paths]
    assert (ROOT / kit.SIGNER) == WORKFLOW and WORKFLOW.is_file()
    with pytest.raises(kit.ReleaseFilesError, match=f'{INSTALLER} has no attestation'):
        kit.check_attestations(paths, kit.REPOSITORY, run=lambda command: SimpleNamespace(returncode=1),
                               which=lambda name: 'gh')
    with pytest.raises(FileNotFoundError, match='gh'):
        kit.check_attestations(paths, kit.REPOSITORY, which=lambda name: None)


def test_the_command_line_says_what_it_found(draft, capsys, monkeypatch):
    assert kit.main(['--dir', str(draft), '--tag', TAG, '--no-attestation']) == 0
    assert capsys.readouterr().out == f'The release files for {TAG} are as built.\n'
    assert kit.main(['--dir', str(draft / 'gone'), '--tag', TAG, '--no-attestation']) == 1
    assert 'There is no folder' in capsys.readouterr().err
    monkeypatch.setattr(kit.shutil, 'which', lambda name: None)
    assert kit.main(['--dir', str(draft), '--tag', TAG]) == 2


# ---- the notes ------------------------------------------------------------------------------------------------------------

CHANGELOG = """# Changelog

## [Unreleased]

### Added

- Something not released yet.

## [1.2.3] - 2026-12-01

### Added

- **Send to Voice Lab.** See the [clip format](docs/clip-format.md#voice-slate-handoff-files), the
  [guide](https://example.com/guide) and ![a picture](docs/images/handoff.png "Save for Voice Lab").

## [1.2.2] - 2026-11-30

- An older line.

[1.2.2]: ../../compare/v1.2.1...v1.2.2
"""


def write_changelog(tmp_path, text=CHANGELOG):
    path = tmp_path / 'CHANGELOG.md'
    path.write_text(text, encoding='utf-8')
    return path


def test_the_notes_are_the_versions_section_with_links_at_its_tag(tmp_path):
    notes = release_notes.notes('1.2.3', TAG, changelog=write_changelog(tmp_path))
    assert notes == (
        '### Added\n\n'
        '- **Send to Voice Lab.** See the [clip format](https://github.com/Fel101/fels-voice-slate/blob/v1.2.3/'
        'docs/clip-format.md#voice-slate-handoff-files), the\n'
        '  [guide](https://example.com/guide) and ![a picture](https://github.com/Fel101/fels-voice-slate/raw/v1.2.3/'
        'docs/images/handoff.png "Save for Voice Lab").\n')
    assert release_notes.notes('1.2.2', changelog=write_changelog(tmp_path)) == '- An older line.\n'


@pytest.mark.parametrize('text, problem', [
    (CHANGELOG.replace('## [1.2.3] - 2026-12-01', '## [1.2.4] - 2026-12-01'), 'no section for 1.2.3'),
    (CHANGELOG.replace('## [1.2.3] - 2026-12-01', '## [1.2.3]'), 'needs its date'),
    (CHANGELOG.replace('## [1.2.3] - 2026-12-01', '## [1.2.3] - 2026-13-01'), 'needs its date'),
    (CHANGELOG.replace('## [1.2.2] - 2026-11-30', '## [1.2.3] - 2026-11-30'), '2 sections for 1.2.3'),
    (CHANGELOG.replace('docs/clip-format.md', '../elsewhere.md'), 'leads out of the repository'),
])
def test_notes_that_cannot_be_written_say_why(tmp_path, text, problem):
    with pytest.raises(release_notes.NotesError, match=re.escape(problem)):
        release_notes.notes('1.2.3', TAG, changelog=write_changelog(tmp_path, text))


def test_an_empty_section_is_refused(tmp_path):
    start, end = CHANGELOG.index('### Added\n\n- **Send'), CHANGELOG.index('## [1.2.2]')
    with pytest.raises(release_notes.NotesError, match='is empty'):
        release_notes.notes('1.2.3', TAG, changelog=write_changelog(tmp_path, CHANGELOG[:start] + CHANGELOG[end:]))


def test_a_tag_must_be_v_and_the_programs_version(capsys):
    from recorder import __version__

    assert release_notes.program_version() == __version__
    with pytest.raises(release_notes.NotesError, match=f'is not v{__version__}'):
        release_notes.check_tag('v9.9.9', __version__)
    assert release_notes.main(['--tag', 'v9.9.9']) == 1
    assert 'recorder/__init__.py' in capsys.readouterr().err


def test_every_released_version_in_the_changelog_has_notes():
    text = release_notes.CHANGELOG.read_text(encoding='utf-8')
    versions = [match['version'] for match in map(release_notes.HEADING.fullmatch, text.splitlines())
                if match and match['version'] != 'Unreleased']
    assert '0.7.0' in versions
    for version in versions:
        assert release_notes.notes(version).strip(), version


# ---- the workflow -----------------------------------------------------------------------------------------------------------

def jobs(text):
    """Each job's own text, by name, from the workflow's `jobs:` block."""
    block = text.split('\njobs:\n', 1)[1]
    parts = re.split(r'\n  (?=[a-z]+:\n)', '\n' + block)
    return {part.split(':', 1)[0].strip(): part for part in parts if part.strip()}


def test_the_release_is_published_only_after_its_files_are_built_attested_and_checked():
    text = WORKFLOW.read_text(encoding='utf-8')
    found = jobs(text)
    assert list(found) == ['tests', 'draft', 'check', 'publish']
    assert 'uses: ./.github/workflows/tests.yml' in found['tests']
    assert 'needs: tests' in found['draft'] and 'needs: draft' in found['check'] and 'needs: check' in found['publish']
    draft = found['draft']
    order = [draft.index(step) for step in ('tools/release_notes.py --tag', 'tools/build_installer.py', 'actions/attest@v4',
                                             'gh release create')]
    assert order == sorted(order)
    assert 'build/Fels-Voice-Slate-*-Setup.exe' in draft and 'build/SHA256SUMS' in draft
    assert all(permission in draft for permission in ('id-token: write', 'attestations: write', 'artifact-metadata: write'))
    assert '--draft' in draft and 'already published' in draft
    assert 'tools/check_release_files.py --dir' in found['check'] and '--no-attestation' not in found['check']
    assert '--draft=false' in found['publish'] and '--draft=false' not in text.split('\n  publish:')[0]
    assert text.split('\njobs:')[0].count('contents: read') == 1          # nothing can write unless a job asks


def test_the_tests_can_be_run_on_a_tag_by_the_release():
    text = (ROOT / '.github' / 'workflows' / 'tests.yml').read_text(encoding='utf-8')
    assert 'workflow_call:' in text and 'ref: ${{ inputs.ref }}' in text
