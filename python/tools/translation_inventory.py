"""List remaining Han text; audit only, never rewrite strings mechanically.

Han characters may be Japanese text, identifiers, Unicode coverage tests, or
input compatibility markers. Review each result before translating it.
"""
from pathlib import Path
import json
import re

HAN = re.compile(r'[\u3400-\u4dbf\u4e00-\u9fff\U00020000-\U0002ebef]')
SKIP = {'.git', 'node_modules', '__pycache__', '.next', 'dist', '.venv'}


def inventory(root):
    rows = []
    for path in sorted(root.rglob('*')):
        if not path.is_file() or SKIP.intersection(path.relative_to(root).parts):
            continue
        try:
            content = path.read_text(encoding='utf-8')
        except (UnicodeError, OSError):
            continue
        lines = [number for number, text in enumerate(content.splitlines(), 1) if HAN.search(text)]
        if lines:
            rows.append({'path': str(path.relative_to(root)), 'lines': lines,
                         'codepoints': len(HAN.findall(content))})
    return rows


if __name__ == '__main__':
    root = Path(__file__).resolve().parents[2]
    print(json.dumps(inventory(root), indent=2, ensure_ascii=True))
