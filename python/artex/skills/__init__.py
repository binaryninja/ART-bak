"""Offline skill metadata primitives. Skill bodies are inert, untrusted text."""
from __future__ import annotations

from dataclasses import dataclass, field
import json
import posixpath
import unicodedata
from typing import Iterable, Mapping

MAX_SKILL_PATH_LEN = 512
MAX_SKILL_ZIP_BYTES = 20 << 20
MAX_SKILL_TOTAL_BYTES = 100 << 20
MAX_SKILL_FILE_BYTES = 20 << 20
MAX_SKILL_ENTRIES = 4000


def valid_skill_name(name: str) -> bool:
    """Mirror server.validSkillName, retaining Unicode identifiers as data."""
    def letter(c: str) -> bool:
        return 'a' <= c <= 'z' or ord(c) > 127 and unicodedata.category(c).startswith('L')
    return (isinstance(name, str) and 1 <= len(name) <= 64
            and letter(name[0]) and not name.endswith('-') and '--' not in name
            and all(letter(c) or '0' <= c <= '9' or c == '-'
                    or ord(c) > 127 and unicodedata.category(c) == 'Nd' for c in name))


def skill_rel_path(value: str) -> str:
    """Return normalized POSIX relative path, or raise ValueError before any I/O."""
    try:
        size = len(value.encode('utf-8'))
    except (AttributeError, UnicodeEncodeError) as exc:
        raise ValueError('invalid path: not valid UTF-8') from exc
    if not value or size > MAX_SKILL_PATH_LEN:
        raise ValueError('invalid path: empty or too long')
    for c in value:
        if (ord(c) < 32 or ord(c) == 127 or c == '\ufffd' or c in '\\%#?*:"<>|'
                or unicodedata.category(c) in {'Cf', 'Co', 'Cs'}
                or c != ' ' and c.isspace()):
            raise ValueError('invalid path: illegal character')
    if '..' in value:
        raise ValueError("invalid path: '..' not allowed")
    if value.startswith('/') or '//' in value:
        raise ValueError('invalid path: must be relative with no empty segments')
    clean = posixpath.normpath(value)
    if clean == '.' or clean.startswith('..'):
        raise ValueError('invalid path')
    return clean


def clean_strs(values: Iterable[str]) -> list[str]:
    return [value.strip() for value in values if value.strip()]


def _split(content: str) -> tuple[list[str], int]:
    lines = content.split('\n')
    if len(lines) < 2 or lines[0].strip() != '---':
        raise ValueError('SKILL.md has no YAML frontmatter')
    for i in range(1, len(lines)):
        if lines[i].strip() == '---':
            return lines, i
    raise ValueError('SKILL.md frontmatter is not closed')


def skill_name_from_frontmatter(content: str) -> str:
    """Mirror the Go upload name extractor (not a general YAML parser)."""
    inside = False
    for line in content.split('\n'):
        line = line.strip()
        if line == '---':
            if inside:
                break
            inside = True
        elif inside and line.startswith('name:'):
            return line[5:].strip().strip('\"\'')
    return ''


def rewrite_skill_frontmatter(content: str, *, mcps: Iterable[str] | None = None,
                              description: str | None = None, license: str | None = None,
                              compatibility: str | None = None) -> str:
    """Port the Go line-oriented rewrite; None means omitted, empty means clear.

    Existing metadata is preserved with Go's line normalization, not YAML semantic
    interpretation. Newly supplied values must be single-line to prevent injection.
    """
    lines, end = _split(content)
    pairs: list[list[str]] = []
    for line in lines[1:end]:
        if ':' in line:
            key, value = line.split(':', 1)
            pairs.append([key.strip(), value.strip()])
        elif line.strip():
            pairs.append(['', line])
    for key, value in [('description', description), ('license', license), ('compatibility', compatibility)]:
        if value is None:
            continue
        _single_line(value)
        for pair in pairs:
            if pair[0] == key:
                pair[1] = value.strip()
                break
        else:
            pairs.append([key, value.strip()])
    if mcps is not None:
        cleaned = clean_strs(mcps)
        for value in cleaned:
            _single_line(value)
        pairs = [pair for pair in pairs if pair[0] != 'mcps']
        if cleaned:
            pairs.append(['mcps', ', '.join(cleaned)])
    return '---\n' + ''.join((f'{k}: {v}' if k else v) + '\n' for k, v in pairs) + '---\n' + '\n'.join(lines[end + 1:])


def _single_line(value: str) -> None:
    if any(c in value for c in '\r\n\x00'):
        raise ValueError('metadata values must be single-line text')


def render_skill(name: str, description: str, *, license: str = '', compatibility: str = '',
                 mcps: Iterable[str] = (), instructions: str = '') -> str:
    """Generate inert SKILL.md text, without creating directories or executing it."""
    if not valid_skill_name(name):
        raise ValueError('invalid skill name')
    if not description.strip():
        raise ValueError('description is required')
    # JSON strings are also YAML scalars: quoting avoids colon/comment injection.
    pairs = [('name', name), ('description', description), ('license', license),
             ('compatibility', compatibility), ('mcps', ', '.join(clean_strs(mcps)))]
    front = []
    for key, value in pairs:
        if value:
            _single_line(value)
            front.append(f'{key}: {json.dumps(value, ensure_ascii=False)}\n')
    body = instructions if instructions.strip() else f'## {name}\n\n<!-- Describe step-by-step instructions in Markdown. -->\n\n1. \n2. \n3. \n'
    return '---\n' + ''.join(front) + '---\n' + body


@dataclass(frozen=True)
class Skill:
    name: str
    description: str
    instructions: str = ''
    license: str = ''
    compatibility: str = ''
    mcps: tuple[str, ...] = ()
    metadata: Mapping[str, str] = field(default_factory=dict)


def parse_skill(content: str) -> Skill:
    """Parse the flat scalar subset used by this port. Unsupported YAML fails closed."""
    lines, end = _split(content)
    values: dict[str, str] = {}
    for line in lines[1:end]:
        if not line.strip() or line.lstrip().startswith('#'):
            continue
        if line[0].isspace() or ':' not in line:
            raise ValueError('only flat scalar frontmatter is supported')
        key, raw = (part.strip() for part in line.split(':', 1))
        if not key or key in values:
            raise ValueError('empty or duplicate metadata key')
        if raw.startswith('"'):
            try:
                raw = json.loads(raw)
            except ValueError as exc:
                raise ValueError('invalid quoted scalar') from exc
        elif raw.startswith("'"):
            if len(raw) < 2 or not raw.endswith("'"):
                raise ValueError('invalid quoted scalar')
            raw = raw[1:-1].replace("''", "'")
        elif raw.startswith(('|', '>', '[', '{', '&', '*', '!')):
            raise ValueError('unsupported YAML scalar or structure')
        else:
            raw = raw.split(' #', 1)[0].rstrip()
        values[key] = raw
    if not valid_skill_name(values.get('name', '')) or not values.get('description', '').strip():
        raise ValueError('valid name and description are required')
    return Skill(values['name'], values['description'], '\n'.join(lines[end + 1:]),
                 values.get('license', ''), values.get('compatibility', ''),
                 tuple(clean_strs(values.get('mcps', '').split(','))), dict(values))


class Catalog:
    """Explicit in-memory registry. No file scanning, plugin imports, or invocation."""
    def __init__(self, skills: Iterable[Skill] = ()) -> None:
        self._skills: dict[str, Skill] = {}
        for skill in skills:
            self.add(skill)

    def add(self, skill: Skill) -> None:
        if not valid_skill_name(skill.name):
            raise ValueError('invalid skill name')
        if skill.name in self._skills:
            raise ValueError(f'duplicate skill: {skill.name}')
        self._skills[skill.name] = skill

    def get(self, name: str) -> Skill | None:
        return self._skills.get(name)

    def list(self) -> list[Skill]:
        return [self._skills[name] for name in sorted(self._skills)]

    def select(self, names: Iterable[str]) -> Catalog:
        return Catalog(self._skills[name] for name in dict.fromkeys(names) if name in self._skills)
