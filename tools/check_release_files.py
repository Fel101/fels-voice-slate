"""Write and check a release's SHA256SUMS, and check its files as they were downloaded from the draft release.

    python tools/check_release_files.py --dir <folder> --tag v1.0.0
    python tools/check_release_files.py --dir <folder> --tag v1.0.0 --no-attestation     the files only, without gh

The release workflow (.github/workflows/release.yml) builds Setup.exe and SHA256SUMS from the tag, has GitHub attest both,
puts them on a draft release, downloads them from the draft into a folder of their own on another runner, and runs this
before it publishes anything. It checks that:

- the folder holds Fels-Voice-Slate-<version>-Setup.exe and SHA256SUMS, and nothing else;
- SHA256SUMS names that one file, and its SHA-256 is the file's;
- `gh attestation verify` finds each file attested by this repository's release workflow.

tools/build_installer.py writes SHA256SUMS with write_sums(). Voice Lab's tools/check_release_files.py does the same for its
own release, so the apps of Fel's Studio are checked alike.

Exit status: 0 when all of that holds; 1 with the reason when something does not; 2 when gh is needed and missing.
"""
import argparse
import hashlib
import re
import shutil
import subprocess
import sys
from pathlib import Path

REPOSITORY = 'Fel101/fels-voice-slate'
SUMS = 'SHA256SUMS'
SIGNER = '.github/workflows/release.yml'        # the workflow whose attestations are accepted
SUM_LINE = re.compile(r'(?P<digest>[0-9a-f]{64}) [ *](?P<name>[^/\\]+)')


class ReleaseFilesError(Exception):
    """A release file is missing, unexpected, changed or not attested; the message says which and what to do."""


def installer_name(tag):
    if not re.fullmatch(r'v\d+\.\d+\.\d+', tag or ''):
        raise ReleaseFilesError(f'{tag!r} is not a release tag such as v1.0.0.')
    return f'Fels-Voice-Slate-{tag[1:]}-Setup.exe'


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as handle:
        for block in iter(lambda: handle.read(1 << 20), b''):
            digest.update(block)
    return digest.hexdigest()


def write_sums(installer):
    """Write SHA256SUMS beside the installer, in the form `sha256sum --check` reads, and return its path."""
    installer = Path(installer)
    sums = installer.with_name(SUMS)
    sums.write_bytes(f'{sha256(installer)}  {installer.name}\n'.encode('ascii'))
    return sums


def check_files(folder, tag):
    """The files the folder must hold, after checking their names and SHA256SUMS. Raises ReleaseFilesError."""
    folder = Path(folder)
    installer = installer_name(tag)
    if not folder.is_dir():
        raise ReleaseFilesError(f"There is no folder {folder}: download the draft's files into it first.")
    found = sorted(path.name for path in folder.iterdir())
    expected = sorted([installer, SUMS])
    if found != expected:
        missing = [name for name in expected if name not in found]
        extra = [name for name in found if name not in expected]
        parts = (['missing ' + ', '.join(missing)] if missing else []) + (['not expected ' + ', '.join(extra)] if extra else [])
        raise ReleaseFilesError(f"The release for {tag} should have {installer} and {SUMS} only: {'; '.join(parts)}.")
    try:
        lines = [line for line in (folder / SUMS).read_text(encoding='utf-8').splitlines() if line.strip()]
    except UnicodeDecodeError:
        lines = []
    entries = [SUM_LINE.fullmatch(line) for line in lines]
    if len(entries) != 1 or not entries[0] or entries[0]['name'] != installer:
        raise ReleaseFilesError(f'{SUMS} should hold one line, the SHA-256 of {installer}; it holds {len(lines)}.')
    actual = sha256(folder / installer)
    if entries[0]['digest'] != actual:
        raise ReleaseFilesError(f"{installer} changed after {SUMS} was written: {SUMS} says {entries[0]['digest']}, the file "
                                f'is {actual}. Delete the draft and run the release again.')
    return [folder / installer, folder / SUMS]


def attestation_command(gh, path, repository):
    return [gh, 'attestation', 'verify', str(path), '--repo', repository, '--signer-workflow', f'{repository}/{SIGNER}']


def check_attestations(paths, repository, *, run=None, which=None):
    run, which = run or subprocess.run, which or shutil.which      # looked up now, so that a test can stand in for them
    gh = which('gh')
    if not gh:
        raise FileNotFoundError("gh, GitHub's command line, is needed to check the attestations; install it or pass "
                                '--no-attestation.')
    for path in paths:
        if run(attestation_command(gh, path, repository)).returncode:
            raise ReleaseFilesError(f"{path.name} has no attestation from {repository}'s release workflow (its message is "
                                    'above). Do not publish the draft.')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('--dir', required=True, type=Path, help="the folder the draft's files were downloaded into")
    parser.add_argument('--tag', required=True, help="the release's tag, such as v1.0.0")
    parser.add_argument('--repo', default=REPOSITORY, help=f'owner/name (default: {REPOSITORY})')
    parser.add_argument('--no-attestation', action='store_true', help='check the names and SHA256SUMS only')
    args = parser.parse_args(argv)
    try:
        paths = check_files(args.dir, args.tag)
        if not args.no_attestation:
            check_attestations(paths, args.repo)
    except ReleaseFilesError as error:
        print(error, file=sys.stderr)
        return 1
    except FileNotFoundError as error:
        print(error, file=sys.stderr)
        return 2
    print(f'The release files for {args.tag} are as built' + ('.' if args.no_attestation else ', and both are attested.'))
    return 0


if __name__ == '__main__':
    sys.exit(main())
