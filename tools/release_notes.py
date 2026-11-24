"""Check a release's tag against the program's version, and write the release's notes from CHANGELOG.md.

    python tools/release_notes.py                         the notes for the version in recorder/__init__.py
    python tools/release_notes.py 0.7.0                   the notes for another version
    python tools/release_notes.py --tag v1.0.0 --output notes.md

The release workflow (.github/workflows/release.yml) runs this first, with --tag, so that a tag that is not "v" and the
version is refused before anything is built. The notes are used when nobody wrote a draft release for the tag by hand: they
are the version's section of the changelog, with each link into the repository pointed at the file as it is at the tag,
because a link such as [Clip format](docs/clip-format.md) leads nowhere on GitHub's release page.

Exit status 1, with the reason, when the version has no section in the changelog, its heading has no date, or its section is
empty.
"""
import argparse
import datetime
import posixpath
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CHANGELOG = ROOT / 'CHANGELOG.md'
REPOSITORY = 'Fel101/fels-voice-slate'
HEADING = re.compile(r'## \[(?P<version>[^\]]+)\](?:\s+-\s+(?P<date>.*?))?\s*')
# An inline link or image whose target has no spaces, with an optional title. The changelog uses no other kind in a section.
LINK = re.compile(r'(?P<label>!?\[[^\]]*\])\((?P<target>[^)\s]+)(?P<title>\s+"[^"]*")?\)')
SCHEME = re.compile(r'[A-Za-z][A-Za-z0-9+.-]*:')


class NotesError(Exception):
    """The notes cannot be written; the message says why and what to change."""


def program_version():
    text = (ROOT / 'recorder' / '__init__.py').read_text(encoding='utf-8')
    return text.split("__version__ = '")[1].split("'")[0]


def check_tag(tag, version):
    if tag != f'v{version}':
        raise NotesError(f'The tag {tag} is not v{version}, the version in recorder/__init__.py. Tag the commit that sets '
                         'this version.')


def section(text, version):
    """The text under the version's heading, up to the next heading of the same level, without its blank edges."""
    lines = text.splitlines()
    found = [index for index, line in enumerate(lines)
             if (match := HEADING.fullmatch(line)) and match['version'] == version]
    if not found:
        raise NotesError(f'CHANGELOG.md has no section for {version}. Give it the heading "## [{version}] - YYYY-MM-DD".')
    if len(found) > 1:
        raise NotesError(f'CHANGELOG.md has {len(found)} sections for {version}; keep one.')
    start = found[0]
    date = HEADING.fullmatch(lines[start])['date']
    try:
        if not date or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', date):
            raise ValueError
        datetime.date.fromisoformat(date)
    except ValueError:
        raise NotesError(f'The heading for {version} in CHANGELOG.md needs its date, written as '
                         f'"## [{version}] - YYYY-MM-DD".') from None
    end = next((index for index in range(start + 1, len(lines)) if lines[index].startswith('## ')), len(lines))
    body = '\n'.join(line for line in lines[start + 1:end] if not re.match(r'\[[^\]]+\]: ', line)).strip('\n')
    if not body.strip():
        raise NotesError(f'The section for {version} in CHANGELOG.md is empty.')
    return body + '\n'


def absolute_links(text, repository, tag):
    """The text with every link into the repository pointed at the file as it is at the tag, and an image at the file's
    raw bytes so that it still shows."""

    def point(match):
        target = match['target']
        if SCHEME.match(target):
            return match[0]
        path, hash_, anchor = target.partition('#')
        path = posixpath.normpath(path.lstrip('/')) if path else 'CHANGELOG.md'
        if path == '..' or path.startswith('../'):
            raise NotesError(f'The link to {target} leads out of the repository.')
        kind = 'raw' if match['label'].startswith('!') else 'blob'
        return f"{match['label']}(https://github.com/{repository}/{kind}/{tag}/{path}{hash_}{anchor}{match['title'] or ''})"

    return LINK.sub(point, text)


def notes(version, tag=None, repository=REPOSITORY, changelog=CHANGELOG):
    return absolute_links(section(Path(changelog).read_text(encoding='utf-8'), version), repository, tag or f'v{version}')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('version', nargs='?', help='the version, such as 1.0.0 (default: the one in recorder/__init__.py)')
    parser.add_argument('--tag', help="the release's tag, which must be v and the program's version")
    parser.add_argument('--repo', default=REPOSITORY, help=f'owner/name (default: {REPOSITORY})')
    parser.add_argument('--output', type=Path, help='write the notes here instead of printing them')
    args = parser.parse_args(argv)
    version = args.version or program_version()
    try:
        if args.tag is not None:
            check_tag(args.tag, program_version())
            version = args.tag[1:]
        text = notes(version, args.tag, args.repo)
    except NotesError as error:
        print(error, file=sys.stderr)
        return 1
    if args.output:
        args.output.write_text(text, encoding='utf-8')
    else:
        sys.stdout.reconfigure(encoding='utf-8')
        sys.stdout.write(text)
    return 0


if __name__ == '__main__':
    sys.exit(main())
