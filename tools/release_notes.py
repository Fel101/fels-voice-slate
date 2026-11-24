"""Check a release's tag against the program's version, and write the release's notes from CHANGELOG.md.

    python tools/release_notes.py                         the notes for the version in recorder/__init__.py
    python tools/release_notes.py 0.7.0                   the notes for another version
    python tools/release_notes.py --tag v1.0.0 --output notes.md

The release workflow (.github/workflows/release.yml) runs this first, with --tag, so that a tag that is not "v" and the
version is refused before anything is built. The notes are used when nobody wrote a draft release for the tag by hand: they
are the version's section of the changelog, with its wrapped lines joined and each link into the repository pointed at the
file as it is at the tag, because GitHub's release page shows every line break and a link such as
[Clip format](docs/clip-format.md) leads nowhere there.

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
# For unwrap(): what starts a block of its own, and what never takes the next line
FENCE = re.compile(r' {0,3}(```|~~~)')
LIST_ITEM = re.compile(r'\s*([-*+]|\d+[.)])\s')
BLOCK = re.compile(r'\s*(#|>|\||[-*+]\s|\d+[.)]\s)')
STANDS_ALONE = re.compile(r'\s*(#|\||```|~~~)')


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


def unwrap(text):
    """The text with each wrapped paragraph and list item on one line. GitHub shows every line break in a release's notes as
    a break, so the changelog's lines, wrapped at 120 characters to read well in the file, would end raggedly there. A line
    stays on its own when it or the line before is blank, when it starts a heading, list item, quote, table row or code
    fence, when the line before is a heading, table row, fence or indented code, or ends in a hard break, and inside a fence."""
    lines, fenced = [], False
    for line in text.split('\n'):
        if FENCE.match(line):
            fenced = not fenced
            lines.append(line)
            continue
        before = lines[-1] if lines else ''
        if (fenced or not line.strip() or not before.strip() or BLOCK.match(line) or STANDS_ALONE.match(before)
                or before.endswith(('  ', '\\'))
                or (before.startswith('    ') and not LIST_ITEM.match(before))):
            lines.append(line)
        else:
            lines[-1] = f'{before.rstrip()} {line.strip()}'
    return '\n'.join(lines)


def notes(version, tag=None, repository=REPOSITORY, changelog=CHANGELOG):
    text = unwrap(section(Path(changelog).read_text(encoding='utf-8'), version))
    return absolute_links(text, repository, tag or f'v{version}')


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
