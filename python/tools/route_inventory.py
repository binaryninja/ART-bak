#!/usr/bin/env python3
"""Generate source-only Go/Python route coverage; never import application code.

Supports the explicit ServeMux registrations, string concatenations, literal
string loop and literal {path, kind} table used by this checkout. Python routes
are resolved with ast from literal assignments/for loops and concatenations.
Unsupported route expressions fail the audit instead of silently claiming parity.
This is a narrow static inventory, not a Go parser or an execution proof.
"""
from __future__ import annotations

import argparse
import ast
from collections import Counter
import hashlib
import itertools
import json
from pathlib import Path
import re
import sys


class AuditError(ValueError):
    pass


def strip_go_comments(text: str) -> str:
    """Preserve line numbers and quoted/raw literals while blanking comments."""
    token = re.compile(r'"(?:\\.|[^"\\])*"|`[^`]*`|\'(?:\\.|[^\'\\])*\'|//[^\n]*|/\*[\s\S]*?\*/')
    return token.sub(lambda m: re.sub(r'[^\n]', ' ', m[0]) if m[0].startswith(('//', '/*')) else m[0], text)


def first_argument(text: str) -> tuple[str, str]:
    quote = None
    escaped = False
    depth = 0
    for index, char in enumerate(text):
        if quote:
            if escaped:
                escaped = False
            elif char == '\\' and quote != '`':
                escaped = True
            elif char == quote:
                quote = None
        elif char in ('"', "'", '`'):
            quote = char
        elif char in '([{':
            depth += 1
        elif char in ')]}':
            depth -= 1
        elif char == ',' and depth == 0:
            return text[:index].strip(), text[index + 1:].strip()
    raise AuditError('Registration arguments must be on one line in this inventory grammar')


def go_strings(expression: str, environment: dict[str, list[str]]) -> list[str]:
    # '+' inside a quoted string must not be treated as concatenation.
    parts = re.findall(r'"(?:\\.|[^"\\])*"|`[^`]*`|[A-Za-z_]\w*(?:\.\w+)*|\+', expression)
    position = 0
    for match in re.finditer(r'"(?:\\.|[^"\\])*"|`[^`]*`|[A-Za-z_]\w*(?:\.\w+)*|\+', expression):
        if expression[position:match.start()].strip():
            raise AuditError(f'Unsupported Go route expression: {expression}')
        position = match.end()
    if expression[position:].strip():
        raise AuditError(f'Unsupported Go route expression: {expression}')
    values = ['']
    want_term = True
    for part in parts:
        if part == '+':
            if want_term:
                raise AuditError(f'Invalid concatenation: {expression}')
            want_term = True
            continue
        if not want_term:
            raise AuditError(f'Missing concatenation: {expression}')
        if part.startswith('"'):
            alternatives = [json.loads(part)]
        elif part.startswith('`'):
            alternatives = [part[1:-1]]
        else:
            if part not in environment:
                raise AuditError(f'Unresolved Go route name {part}: {expression}')
            alternatives = environment[part]
        values = [left + right for left, right in itertools.product(values, alternatives)]
        want_term = False
    if want_term:
        raise AuditError(f'Empty/incomplete route expression: {expression}')
    return values


def go_inventory(root: Path) -> tuple[list[dict], list[dict], dict[str, str], int]:
    routes, mounts, hashes = [], [], {}
    registrations = 0
    for source in sorted((root / 'server').glob('*.go')):
        if source.name.endswith('_test.go'):
            continue
        raw = source.read_text()
        text = strip_go_comments(raw)
        if not re.search(r'\.Handle(?:Func)?\s*\(', text):
            continue
        relative = source.relative_to(root).as_posix()
        hashes[relative] = hashlib.sha256(raw.encode()).hexdigest()
        environment: dict[str, list[str]] = {}
        function = ''
        for number, line in enumerate(text.splitlines(), 1):
            definition = re.match(r'func (?:\([^)]*\) )?(\w+)\(', line)
            if definition:
                function = definition[1]
                environment = {}
            assignment = re.match(r'\s*(\w+)\s*:=\s*("(?:\\.|[^"\\])*"|`[^`]*`)\s*$', line)
            if assignment:
                environment[assignment[1]] = go_strings(assignment[2], {})
            string_loop = re.match(r'\s*for _, (\w+) := range \[\]string\{(.*?)\} \{\s*$', line)
            if string_loop:
                literal_body = string_loop[2]
                values = re.findall(r'"(?:\\.|[^"\\])*"', literal_body)
                residue = re.sub(r'"(?:\\.|[^"\\])*"', '', literal_body).replace(',', '').strip()
                if residue:
                    raise AuditError(f'Unsupported string loop at {relative}:{number}')
                environment[string_loop[1]] = [json.loads(value) for value in values]
            table_loop = re.match(r'\s*for _, (\w+) := range \[\]struct\{ path, kind string \}\{(.*)\} \{\s*$', line)
            if table_loop:
                pairs = re.findall(r'\{("(?:\\.|[^"\\])*"),\s*("(?:\\.|[^"\\])*")\}', table_loop[2])
                residue = re.sub(r'\{("(?:\\.|[^"\\])*"),\s*("(?:\\.|[^"\\])*")\}', '', table_loop[2]).replace(',', '').strip()
                if not pairs or residue:
                    raise AuditError(f'Unsupported route table at {relative}:{number}')
                environment[table_loop[1] + '.path'] = [json.loads(pair[0]) for pair in pairs]
            call = re.search(r'\b(\w+)\.Handle(Func)?\s*\((.*)', line)
            if not call:
                continue
            registrations += 1
            expression, rest = first_argument(call[3])
            handler_match = re.match(r'(s\.\w+|\w+)', rest)
            handler = handler_match[1] if handler_match else 'unresolved'
            if rest.startswith('func('):
                handler = 'inline closure: s.handleSideQuestions'
            for pattern in go_strings(expression, environment):
                item = {'pattern': pattern, 'source': relative, 'line': number,
                        'function': function, 'handler': handler, 'expression': expression}
                if re.fullmatch(r'[A-Z]+ /.*', pattern):
                    method, path = pattern.split(' ', 1)
                    routes.append({**item, 'method': method, 'path': path})
                elif pattern.startswith('/'):
                    mounts.append(item)
                else:
                    raise AuditError(f'Invalid route pattern at {relative}:{number}: {pattern!r}')
    return routes, mounts, hashes, registrations


def literal(node: ast.AST, environment: dict):
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.Name) and node.id in environment:
        return environment[node.id]
    if isinstance(node, (ast.List, ast.Tuple)):
        values = [literal(n, environment) for n in node.elts]
        return tuple(values) if isinstance(node, ast.Tuple) else values
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left, right = literal(node.left, environment), literal(node.right, environment)
        if isinstance(left, str) and isinstance(right, str):
            return left + right
    if isinstance(node, ast.Compare) and len(node.ops) == 1 and len(node.comparators) == 1:
        left, right = literal(node.left, environment), literal(node.comparators[0], environment)
        if isinstance(node.ops[0], ast.NotEq):
            return left != right
        if isinstance(node.ops[0], ast.Eq):
            return left == right
    raise AuditError(f'Unsupported Python route expression: {ast.unparse(node)}')


def bind_target(target: ast.AST, value, environment: dict):
    if isinstance(target, ast.Name):
        environment[target.id] = value
    elif isinstance(target, (ast.Tuple, ast.List)) and isinstance(value, (list, tuple)) and len(target.elts) == len(value):
        for name, item in zip(target.elts, value):
            bind_target(name, item, environment)
    else:
        raise AuditError('Unsupported literal loop target')


def python_inventory(root: Path) -> tuple[list[dict], dict[str, str]]:
    routes, hashes = [], {}
    for source in sorted((root / 'python/artex/server').rglob('*.py')):
        raw = source.read_text()
        relative = source.relative_to(root).as_posix()
        hashes[relative] = hashlib.sha256(raw.encode()).hexdigest()
        tree = ast.parse(raw, filename=relative)
        router_names = {'router'}
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call) and ast.unparse(node.value.func).endswith('Router'):
                router_names.update(t.id for t in node.targets if isinstance(t, ast.Name))

        def has_route(node):
            return any(isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                       and n.func.attr == 'add' and ast.unparse(n.func.value) in router_names
                       for n in ast.walk(node))

        def walk(node, environment, owner=''):
            if isinstance(node, ast.For) and has_route(node):
                values = literal(node.iter, environment)
                if not isinstance(values, (list, tuple)):
                    raise AuditError('Route loop is not a literal sequence')
                for value in values:
                    child_environment = environment.copy()
                    bind_target(node.target, value, child_environment)
                    for child in node.body:
                        walk(child, child_environment, owner)
                if node.orelse:
                    raise AuditError('Route loop else blocks need explicit support')
                return
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                owner = (owner + '.' if owner else '') + node.name
                environment = environment.copy()
            if isinstance(node, ast.Assign):
                try:
                    value = literal(node.value, environment)
                    for target in node.targets:
                        bind_target(target, value, environment)
                except AuditError:
                    pass
            if isinstance(node, ast.If) and has_route(node):
                raise AuditError(f'Conditional route registration needs explicit support: {relative}:{node.lineno}')
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == 'add' and ast.unparse(node.func.value) in router_names:
                if len(node.args) < 3:
                    raise AuditError('Router.add requires literal method/path and a handler')
                method, path = literal(node.args[0], environment), literal(node.args[1], environment)
                if not isinstance(method, str) or not isinstance(path, str) or not path.startswith('/'):
                    raise AuditError('Invalid Python route method/path')
                public = False
                for keyword in node.keywords:
                    if keyword.arg == 'public':
                        public = literal(keyword.value, environment)
                routes.append({'method': method.upper(), 'path': path,
                               'source': relative, 'line': node.lineno, 'function': owner,
                               'handler': ast.unparse(node.args[2]), 'public': public,
                               'status': 'offline contract only; production adapters missing'})
                return
            for child in ast.iter_child_nodes(node):
                walk(child, environment, owner)

        walk(tree, {})
    return routes, hashes


def inventory(root: Path) -> dict:
    go, mounts, go_hashes, registration_calls = go_inventory(root)
    python, python_hashes = python_inventory(root)
    py_index = {(r['method'], r['path']): r for r in python}
    go_keys = {(r['method'], r['path']) for r in go}
    duplicates = [f'{method} {path}' for (method, path), count in
                  Counter((r['method'], r['path']) for r in go).items() if count > 1]
    if duplicates or len(py_index) != len(python):
        raise AuditError(f'Duplicate route registrations: {duplicates or "Python duplicates"}')
    for route in go:
        counterpart = py_index.get((route['method'], route['path']))
        route['python'] = counterpart
        route['status'] = counterpart['status'] if counterpart else 'not registered in Python'
    return {'schema_version': 1,
            'scope': 'Static registration coverage only, not HTTP service availability or behavior parity.',
            'summary': {'go_registration_call_sites': registration_calls,
                        'go_method_path_routes': len(go), 'go_mount_handlers': len(mounts),
                        'python_offline_contract_routes': len(python),
                        'matched_offline_contract_routes': sum(r['python'] is not None for r in go),
                        'go_routes_without_python_registration': sum(r['python'] is None for r in go)},
            'source_sha256': {**go_hashes, **python_hashes},
            'go_routes': go, 'go_mounts': mounts, 'python_routes': python,
            'python_only': [r for r in python if (r['method'], r['path']) not in go_keys]}


def markdown(data: dict) -> str:
    summary = data['summary']
    lines = ['# Generated route coverage inventory', '',
             'Generated by `python python/tools/route_inventory.py`. This reads source only:',
             'it does not import the application, open a listener, contact a database, or call tools.', '',
             '**Registration is not behavioral parity.** The Python matches below are offline',
             'health/auth contracts with injected adapters, not deployed HTTP endpoints.',
             'Missing bcrypt/database/key-storage/transport integration is described in',
             '`artex/server/PORT_STATUS.md`. No route is labeled production-ready.', '',
             '## Counts', '']
    lines += [f'- {name.replace("_", " ")}: **{value}**.' for name, value in summary.items()]
    lines += ['', 'The earlier 252-route figure counted literals and simple concatenations but',
              'missed nine registrations from the side-question nested loop: three parent',
              'paths × three methods. This inventory expands that loop from source.',
              'Go ServeMux implicit HEAD behavior for GET is not counted as an explicit route.', '',
              'The scanner fails on unsupported route expressions rather than silently dropping',
              'them. It handles this checkout\'s registration syntax, not arbitrary Go/Python.',
              'Use `--check` to detect stale output after source changes. Source hashes are',
              'recorded in `ROUTE_PARITY.json`; line references identify the inspected code.', '',
              '## Method/path matrix', '',
              '| Method | Path | Go registration | Python status |',
              '| --- | --- | --- | --- |']
    for route in sorted(data['go_routes'], key=lambda r: (r['path'], r['method'])):
        counterpart = route['python']
        status = (f'Offline contract only: `{counterpart["source"]}:{counterpart["line"]}`'
                  if counterpart else 'Not registered')
        lines.append(f'| {route["method"]} | `{route["path"]}` | `{route["source"]}:{route["line"]}` | {status} |')
    lines += ['', '## Non-method mounts', '']
    for route in data['go_mounts']:
        lines.append(f'- `{route["pattern"]}` → `{route["handler"]}` at `{route["source"]}:{route["line"]}`; no Python transport equivalent.')
    if data['python_only']:
        lines += ['', '## Python-only registrations', '']
        lines += [f'- `{r["method"]} {r["path"]}`' for r in data['python_only']]
    return '\n'.join(lines) + '\n'


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument('--check', action='store_true', help='Check generated files without modifying them')
    parser.add_argument('--stdout', action='store_true', help='Print JSON instead of writing artifacts')
    args = parser.parse_args()
    try:
        data = inventory(args.root)
        encoded = json.dumps(data, indent=2, ensure_ascii=True) + '\n'
        if args.stdout:
            print(encoded, end='')
            return 0
        outputs = {args.root / 'python/ROUTE_PARITY.json': encoded,
                   args.root / 'python/ROUTE_PARITY.md': markdown(data)}
        if args.check:
            stale = [str(path) for path, content in outputs.items()
                     if not path.exists() or path.read_text() != content]
            if stale:
                print('Stale route inventory: ' + ', '.join(stale), file=sys.stderr)
                return 1
        else:
            for path, content in outputs.items():
                path.write_text(content)
        print(json.dumps(data['summary'], sort_keys=True))
        return 0
    except (AuditError, OSError, SyntaxError, ValueError) as exc:
        print(f'Route inventory failed: {exc}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
