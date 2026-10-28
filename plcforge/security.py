from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import tempfile

TEXT_EXTENSIONS = {'.st', '.json', '.md', '.txt', '.xml', '.csv', '.ltl'}
BLOCKED_PARTS = {'.git', '.env', '.plcforge', '.venv', '.aws', '.codex', '.agents',
                 'node_modules', 'reference', '__pycache__'}
REGISTERED_SECRETS = set()


def register_secret(value: str) -> None:
    if len(value)>=8:
        REGISTERED_SECRETS.add(value)


def sensitive_values():
    return set(REGISTERED_SECRETS) | {value for name,value in os.environ.items()
        if re.search(r'(API_KEY|TOKEN|SECRET|PASSWORD)$',name,re.I) and len(value)>=8}


def redact(text: str) -> str:
    for value in sorted(sensitive_values(),key=len,reverse=True):
        text = text.replace(value, '[REDACTED]')
    text = re.sub(r'\bms-[a-zA-Z0-9-]{20,}\b', '[REDACTED]', text)
    return text


def digest(text: str) -> str:
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def write_json(path: Path, value, private: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(value, ensure_ascii=False, indent=2)
    # Preserve model reasoning in private session state; redact secrets in all cases.
    fd, name = tempfile.mkstemp(prefix='.tmp-', dir=str(path.parent))
    try:
        with os.fdopen(fd, 'w', encoding='utf-8', newline='\n') as stream:
            stream.write(redact(data))
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


class Workspace:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def path(self, relative: str) -> Path:
        raw = Path(relative)
        if raw.is_absolute() or ':' in relative or '\x00' in relative:
            raise ValueError('Only workspace-relative paths are allowed')
        path = (self.root / raw).resolve()
        if not path.is_relative_to(self.root):
            raise ValueError('Path escapes workspace')
        parts = path.relative_to(self.root).parts
        if path.name.lower() in ('settings.json','settings.local.json'):
            raise ValueError('LLM settings cannot be accessed by model tools')
        if any(p.lower() in BLOCKED_PARTS or p.startswith('.') or
               re.search(r'(credential|auth\.json|secret|token|password)', p, re.I) for p in parts):
            raise ValueError('Private/configuration paths cannot be accessed by model tools')
        if path.suffix.lower() not in TEXT_EXTENSIONS:
            raise ValueError('Allowed text extensions: ' + ', '.join(sorted(TEXT_EXTENSIONS)))
        return path

    def files(self, pattern: str = '*') -> list[Path]:
        found = []
        for path in self.root.rglob(pattern):
            if path.is_file():
                try:
                    self.path(str(path.relative_to(self.root)))
                except ValueError:
                    continue
                found.append(path)
        return sorted(found)[:500]

    def read(self, relative: str, start_line: int = 1, max_lines: int = 200) -> dict:
        path = self.path(relative)
        if path.stat().st_size > 512000:
            raise ValueError('File exceeds 512 KB')
        if start_line < 1 or not 1 <= max_lines <= 400:
            raise ValueError('Invalid line range')
        text = path.read_text(encoding='utf-8-sig')
        lines = text.splitlines()
        return {'path': relative, 'sha256': digest(text), 'total_lines': len(lines),
                'content': '\n'.join('%d: %s' % (i+1, lines[i])
                                     for i in range(start_line-1, min(len(lines), start_line-1+max_lines)))}

    def write(self, relative: str, content: str) -> dict:
        path = self.path(relative)
        if len(content.encode('utf-8')) > 128000:
            raise ValueError('Write exceeds 128 KB')
        if redact(content) != content:
            raise ValueError('Refusing to write credentials')
        if path.exists():
            previous = path.read_text(encoding='utf-8-sig')
            checkpoint = self.root / '.plcforge' / 'checkpoints' / (digest(previous) + '.txt')
            checkpoint.parent.mkdir(parents=True, exist_ok=True)
            checkpoint.write_text(previous, encoding='utf-8')
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding='utf-8')
        return {'path': relative, 'sha256': digest(content), 'bytes': len(content.encode('utf-8'))}
