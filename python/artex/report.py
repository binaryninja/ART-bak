"""English report exporters ported from report/*.go, with no database access.

Differences: unknown severities sort last instead of Go's implicit rank zero;
filenames truncate at valid UTF-8 boundaries; evidence fences cannot be closed
by evidence containing backticks. CSV text is spreadsheet-formula neutralized.
Free-form report/summary Markdown is preserved, not HTML-sanitized; rendering
applications must sanitize HTML and disallow unsafe link schemes.
"""
from collections import Counter
import csv
from dataclasses import dataclass, field
from datetime import datetime
import io
import json
import re
import unicodedata


@dataclass
class TrafficBinding:
    id: int
    role: str = ""
    method: str = ""
    url: str = ""
    status: int = 0
    note: str = ""


@dataclass
class Finding:
    id: int
    created_at: datetime
    name: str = ""
    vulnclass: str = ""
    severity: str = ""
    status: str = ""
    task_description: str = ""
    summary: str = ""
    evidence: str = ""
    report: str = ""
    evidence_version: int = 0
    report_evidence_version: int = 0
    traffic_bindings: list[TrafficBinding] = field(default_factory=list)


def _nz(value, fallback=""):
    return value if value.strip() else fallback


def _date(value):
    return value.strftime("%Y-%m-%d %H:%M:%S")


def _title(f):
    return _nz(f.name, _nz(f.vulnclass, "Unclassified"))


def _ordered(findings):
    ranks = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    # Stable sorts avoid converting naive datetime values via local timezone.
    return sorted(sorted(findings, key=lambda f: f.created_at, reverse=True),
                  key=lambda f: ranks.get(f.severity, 4))


def _fence(text):
    runs = [len(m.group()) for m in re.finditer(r"`+", text)]
    fence = "`" * max(3, max(runs, default=0) + 1)
    return f"{fence}\n{text}\n{fence}\n\n"


def traffic_markdown(f, attachments=False):
    stale = bool(f.report) and f.evidence_version != f.report_evidence_version
    if not f.traffic_bindings and not stale:
        return ""
    text = f"\n## Linked traffic evidence\n\nEvidence version: {f.evidence_version}; bindings: {len(f.traffic_bindings)}.\n\n"
    if stale:
        text += "Evidence has changed; the detailed report needs updating.\n\n"
    for i, b in enumerate(f.traffic_bindings, 1):
        url = b.url.replace("`", "%60")
        text += f"{i}. **Evidence #{b.id} · {b.role}** — `{b.method} {url}`, status {b.status}\n"
        if b.note:
            text += "   " + b.note.replace("\n", "\n   ") + "\n"
        if attachments:
            text += f"   [Request](evidence/{f.id}/{b.id}/request.http) · [Response](evidence/{f.id}/{b.id}/response.http)\n"
    return text + "\n"


def single_finding_markdown(f, generated_at):
    text = f"# [{_nz(f.severity, 'info').upper()}] {_title(f)}\n\n"
    if f.vulnclass:
        text += f"- **Class**: {f.vulnclass}\n"
    text += f"- **Severity**: {_nz(f.severity, 'info')}\n- **Status**: {_nz(f.status, 'pending')}\n"
    if f.task_description.strip():
        text += f"- **Task**: {f.task_description.strip()}\n"
    text += f"- **Discovered**: {_date(f.created_at)}\n- **Generated**: {_date(generated_at)}\n\n"
    if f.summary.strip():
        text += f"## Summary\n\n{f.summary.strip()}\n\n"
    if f.evidence.strip():
        text += "## Evidence\n\n" + _fence(f.evidence.strip())
    if f.report.strip():
        text += f"## Detailed report\n\n{f.report.strip()}\n"
    return text + traffic_markdown(f, True)


def findings_markdown(findings, generated_at):
    items = _ordered(findings)
    counts = Counter(f.severity for f in items)
    text = f"# Vulnerability findings summary\n\n- **Generated**: {_date(generated_at)}\n- **Total findings**: {len(items)}\n\n## Summary\n\n| Severity | Count |\n| --- | --- |\n"
    for severity in ("critical", "high", "medium", "low"):
        text += f"| {severity.title()} | {counts[severity]} |\n"
    text += "\n"
    if not items:
        return text + "_No matching vulnerabilities._\n"
    text += "## Findings\n\n"
    for i, f in enumerate(items, 1):
        text += f"### {i}. [{_nz(f.severity, 'info').upper()}] {_title(f)}\n\n"
        if f.vulnclass:
            text += f"- **Class**: {f.vulnclass}\n"
        text += f"- **Status**: {_nz(f.status, 'pending')}\n"
        if f.task_description.strip():
            text += f"- **Task**: {f.task_description.strip()}\n"
        text += f"- **Discovered**: {_date(f.created_at)}\n\n"
        if f.summary.strip():
            text += f.summary.strip() + "\n\n"
        if f.evidence.strip():
            text += "**Evidence:**\n\n" + _fence(f.evidence.strip())
        if f.report.strip():
            text += f"**Detailed report:**\n\n{f.report.strip()}\n\n"
        text += traffic_markdown(f) + "---\n\n"
    return text


def finding_filename(f):
    name = f"{_nz(f.severity, 'info')}_{_title(f)}"
    name = "".join(c if c in '._-' or unicodedata.category(c)[0] in 'LN' else '_' for c in name)
    name = re.sub('_+', '_', name).strip('._') or f"finding_{f.id}"
    # Keep the stable numeric ID after truncation to avoid collisions for long titles.
    if type(f.id) is not int:
        raise ValueError('Finding ID must be an integer')
    suffix = f'_{f.id}'
    budget = 120 - len(suffix.encode('utf-8'))
    if budget < 1:
        raise ValueError('Finding ID is too long for a safe filename')
    return name.encode('utf-8')[:budget].decode('utf-8', errors='ignore') + suffix + '.md'


def _csv_safe(value):
    # A quoted CSV cell alone does not prevent spreadsheet formula execution.
    dangerous = value.startswith(("\t", "\r", "\n")) or value.lstrip().startswith(("=", "+", "-", "@"))
    return "'" + value if dangerous else value


def findings_csv(findings):
    out = io.StringIO(newline='')
    writer = csv.writer(out, lineterminator='\n')
    writer.writerow(['ID', 'Name', 'Class', 'Severity', 'Status', 'Task', 'Discovered', 'Summary', 'Traffic evidence count', 'Traffic evidence IDs'])
    for f in _ordered(findings):
        writer.writerow([f.id, *map(_csv_safe, [_title(f), f.vulnclass, _nz(f.severity, 'info'),
                         _nz(f.status, 'pending'), f.task_description, _date(f.created_at), f.summary.strip()]),
                         len(f.traffic_bindings), ','.join(str(b.id) for b in f.traffic_bindings)])
    return out.getvalue().encode('utf-8-sig')


def markdown(title, goal, generated_at, asset_counts, findings):
    """Exploration graph report. findings is an iterable of payload mappings."""
    items = []
    for payload in findings:
        if isinstance(payload, (str, bytes)):
            try:
                payload = json.loads(payload)
            except (ValueError, UnicodeError):
                payload = {}
        items.append(payload if isinstance(payload, dict) else {})
    ranks = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    items.sort(key=lambda f: ranks.get(f.get('severity'), 4)
               if isinstance(f.get('severity'), str) else 4)
    text = f"# Penetration test report — {_nz(title, 'Untitled task')}\n\n- **Task goal**: {_nz(goal, '(unspecified)')}\n- **Generated**: {_date(generated_at)}\n\n## Summary\n\n- Confirmed findings: **{len(items)}**\n- Assets: "
    text += ', '.join(f'{key} {asset_counts[key]}' for key in sorted(asset_counts)) + '\n\n## Findings\n\n'
    if not items:
        return text + '_No vulnerabilities confirmed._\n\n'
    for i, f in enumerate(items, 1):
        get = lambda key: f.get(key) if isinstance(f.get(key), str) else ''
        text += f"### {i}. [{_nz(get('severity'), 'info').upper()}] {_nz(get('name'), _nz(get('vulnclass'), 'Unclassified'))}\n\n{get('summary')}\n\n"
        evidence = f.get('evidence')
        poc = evidence.get('poc') if isinstance(evidence, dict) else None
        if isinstance(poc, str) and poc:
            text += '**PoC / Evidence:**\n\n' + _fence(poc)
    return text
