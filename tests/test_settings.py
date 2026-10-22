import json

import pytest

from recorder import settings


def test_the_environment_variable_wins(tmp_path, monkeypatch):
    target = tmp_path / 'custom.json'
    monkeypatch.setenv(settings.ENV_VAR, str(target))
    assert settings.settings_path() == target


def test_a_settings_file_next_to_the_program_means_portable_mode(tmp_path, monkeypatch):
    monkeypatch.delenv(settings.ENV_VAR, raising=False)
    monkeypatch.setattr(settings, 'program_dir', lambda: tmp_path)
    monkeypatch.setenv('APPDATA', str(tmp_path / 'appdata'))
    assert settings.settings_path() == tmp_path / 'appdata' / 'Fels Voice Slate' / 'settings.json'
    (tmp_path / 'settings.json').write_text('{}', encoding='utf-8')
    assert settings.settings_path() == tmp_path / 'settings.json'


def test_the_settings_folder_and_the_id_have_no_apostrophe():
    """The window shows "Fel's Voice Slate", but a folder name or an id with an apostrophe (or an id with a space) trips scripts."""
    assert "'" not in settings.APP_DIR and "'" not in settings.APP_ID and ' ' not in settings.APP_ID


def test_load_gives_an_empty_dict_for_a_missing_or_unreadable_file(tmp_path):
    assert settings.load(tmp_path / 'none.json') == {}
    bad = tmp_path / 'bad.json'
    bad.write_text('{"root": ', encoding='utf-8')
    assert settings.load(bad) == {}
    bad.write_text('[1, 2]', encoding='utf-8')
    assert settings.load(bad) == {}


def test_save_and_update_keep_other_keys_and_create_the_folder(tmp_path):
    path = tmp_path / 'deep' / 'settings.json'
    settings.save({'root': 'C:/clips', 'ffmpeg': 'C:/ffmpeg.exe'}, path)
    settings.update({'root': 'D:/other', 'tail_seconds': 0.2}, path)
    assert settings.load(path) == {'root': 'D:/other', 'ffmpeg': 'C:/ffmpeg.exe', 'tail_seconds': 0.2}
    settings.update({'ffmpeg': None}, path)
    assert 'ffmpeg' not in settings.load(path)
    assert not list(path.parent.glob('*.tmp'))


def test_a_damaged_file_is_moved_aside_rather_than_overwritten(tmp_path):
    path = tmp_path / 'settings.json'
    path.write_text('{"asr_python": "C:/py', encoding='utf-8')
    settings.update({'root': 'D:/clips'}, path)
    assert settings.load(path) == {'root': 'D:/clips'}
    backups = list(tmp_path.glob('settings.json.damaged-*'))
    assert len(backups) == 1 and backups[0].read_text(encoding='utf-8') == '{"asr_python": "C:/py'


@pytest.mark.parametrize('raw, expected', [(None, 0.5), ('abc', 0.5), (0.3, 0.3), ('0.3', 0.3), (2, 1.0), (-1, 0.0),
                                           (float('nan'), 0.5), (float('inf'), 0.5)])
def test_the_stop_tail_is_read_safely(raw, expected):
    data = {} if raw is None else {'tail_seconds': raw}
    assert settings.tail_seconds(data) == pytest.approx(expected)


@pytest.mark.parametrize('raw, expected', [(None, True), (True, True), (False, False), ('false', True), (0, True), ([], True)])
def test_the_record_key_in_other_programs_is_on_unless_the_settings_say_false(raw, expected):
    data = {} if raw is None else {'global_hotkey': raw}
    assert settings.global_hotkey(data) is expected


@pytest.fixture
def program(tmp_path, monkeypatch):
    """A program folder of its own, with no cloud folder named in the environment."""
    for name in ('OneDrive', 'OneDriveConsumer', 'OneDriveCommercial'):
        monkeypatch.delenv(name, raising=False)
    folder = tmp_path / 'app'
    monkeypatch.setattr(settings, 'program_dir', lambda: folder)
    return folder


def test_the_default_output_folder_is_voice_clips_inside_the_program_folder(program):
    assert settings.default_root() == program / 'voice-clips'
    assert (program / 'voice-clips').is_dir()


def test_the_default_folder_ignores_everything_in_it_in_git(program):
    folder = settings.default_root()
    assert (folder / '.gitignore').read_text(encoding='utf-8').splitlines()[-1] == '*'
    (folder / '.gitignore').write_text('mine\n', encoding='utf-8')
    settings.default_root()
    assert (folder / '.gitignore').read_text(encoding='utf-8') == 'mine\n'


@pytest.mark.parametrize('name', ['OneDrive', 'OneDrive - Contoso', 'Dropbox', 'Google Drive', 'iCloudDrive'])
def test_a_program_folder_that_a_cloud_service_syncs_falls_back_to_the_profile(tmp_path, monkeypatch, name):
    app = tmp_path / name / 'tools' / 'recorder'
    monkeypatch.setattr(settings, 'program_dir', lambda: app)
    assert settings.default_root() == settings.FALLBACK_ROOT
    assert not (app / 'voice-clips').exists()


def test_onedrive_is_also_recognised_from_its_environment_variable(tmp_path, monkeypatch):
    cloud = tmp_path / 'Cloud'
    monkeypatch.setenv('OneDrive', str(cloud))
    assert settings.in_synced_folder(cloud / 'Desktop' / 'app')
    assert not settings.in_synced_folder(tmp_path / 'elsewhere')


def test_a_program_folder_that_cannot_be_written_falls_back_to_the_profile(program, monkeypatch):
    monkeypatch.setattr(settings, '_writable', lambda folder: False)
    assert settings.default_root() == settings.FALLBACK_ROOT


def test_the_fallback_folder_is_under_the_users_profile_not_documents():
    assert settings.FALLBACK_ROOT.parts[-2:] == ('Recordings', 'voice-clips')
    assert 'Documents' not in settings.FALLBACK_ROOT.parts
    assert json.dumps(str(settings.FALLBACK_ROOT))


def test_a_library_without_a_folder_uses_the_default(program):
    from recorder.store import Library
    assert Library().root == program / 'voice-clips'


def test_a_session_without_a_saved_folder_records_into_the_default(program, tmp_path, isolated, clock, player):
    from recorder.jobs import Jobs
    from recorder.session import Session
    session = Session(settings_file=tmp_path / 'settings.json', jobs=Jobs(inline=True), player=player, clock=clock)
    try:
        assert session.lib.root == program / 'voice-clips'
    finally:
        session.jobs.close()
