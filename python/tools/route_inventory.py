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
    if node is None:
        raise AuditError("Missing literal value")
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

        consumed_sequences = []

        def invalidate(target, environment):
            # Invalidate aliases as well: a = routes; a.append(...) mutates routes.
            names = {n.id for n in ast.walk(target) if isinstance(n, ast.Name)}
            values = [environment[name] for name in names if name in environment]
            if any(isinstance(value, (list, tuple)) and any(value is consumed for consumed in consumed_sequences)
                   for value in values):
                raise AuditError(f'Route sequence modified after registration: {relative}:{target.lineno}')
            aliases = {name for name, value in environment.items()
                       if isinstance(value, (list, tuple)) and any(value is old for old in values)}
            for name in names | aliases:
                environment.pop(name, None)

        def invalidate_calls(node, environment):
            if node is None:
                return
            for call in ast.walk(node):
                if not isinstance(call, ast.Call):
                    continue
                candidates = [*call.args, *(kw.value for kw in call.keywords)]
                if isinstance(call.func, ast.Attribute):
                    candidates.append(call.func.value)
                for candidate in candidates:
                    if any(isinstance(name, ast.Name) and isinstance(environment.get(name.id), (list, tuple))
                           for name in ast.walk(candidate)):
                        invalidate(candidate, environment)

        def walk(node, environment, owner=''):
            if isinstance(node, ast.For) and has_route(node):
                values = literal(node.iter, environment)
                if not isinstance(values, (list, tuple)):
                    raise AuditError('Route loop is not a literal sequence')
                consumed_sequences.append(values)
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
            if isinstance(node, (ast.Assign, ast.AnnAssign)):
                if has_route(node):
                    raise AuditError('Route registration embedded in assignment needs explicit support')
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                if any(not isinstance(target, (ast.Name, ast.Tuple, ast.List)) for target in targets):
                    for target in targets:
                        invalidate(target, environment)
                else:
                    try:
                        value = literal(node.value, environment)
                    except AuditError:
                        invalidate_calls(node.value, environment)
                        # Unsupported derived aliases (e.g. routes[0] or routes[:])
                        # may later mutate an existing table; do not trust it.
                        if node.value is not None:
                            for name in ast.walk(node.value):
                                if isinstance(name, ast.Name) and isinstance(environment.get(name.id), (list, tuple)):
                                    invalidate(name, environment)
                        # Never retain an earlier literal after an unsupported write.
                        for target in targets:
                            invalidate(target, environment)
                    else:
                        for target in targets:
                            bind_target(target, value, environment)
                return
            if isinstance(node, (ast.AugAssign, ast.Delete, ast.NamedExpr)):
                targets = node.targets if isinstance(node, ast.Delete) else [node.target]
                for target in targets:
                    invalidate(target, environment)
                return
            if isinstance(node, (ast.If, ast.While, ast.Try, ast.With, ast.Match, ast.For)) and not has_route(node):
                # No control-flow interpreter: a conditional/loop assignment may
                # not be assumed to have run. Mark all affected literals unknown.
                for child in ast.walk(node):
                    if isinstance(child, ast.Name) and isinstance(child.ctx, (ast.Store, ast.Del)):
                        invalidate(child, environment)
                    elif isinstance(child, ast.Call):
                        if isinstance(child.func, ast.Attribute):
                            invalidate(child.func.value, environment)
                        for arg in child.args:
                            if isinstance(arg, ast.Name) and isinstance(environment.get(arg.id), (list, tuple)):
                                invalidate(arg, environment)
                return
            if isinstance(node, (ast.While, ast.Try, ast.With, ast.Match)) and has_route(node):
                raise AuditError(f'Unsupported route control flow: {relative}:{node.lineno}')
            if isinstance(node, ast.If) and has_route(node):
                # This exact typed optional view is audited, not a general
                # control-flow interpreter. Unknown guards/else/mounts fail.
                permitted = {
                    'config': '/api/intercept/tool-config',
                    'rules': '/api/intercept/rules',
                    'judge_usage': '/api/intercept/judge/usage',
                    'judge_config': '/api/intercept/judge',
                    'notification_history': '/api/notify/deliveries',
                    'detail': '/api/intercept/history/{id}',
                    'task_history': '/api/intercept/task/{taskID}',
                }
                test = node.test
                key = test.left.value if isinstance(test, ast.Compare) and isinstance(test.left, ast.Constant) else None
                call = node.body[0].value if len(node.body) == 1 and isinstance(node.body[0], ast.Expr) else None
                allowed = (
                    relative == 'python/artex/server/intercept_read_setup.py'
                    and owner == '_InterceptReadApplication.__init__'
                    and type(key) is str and key in permitted
                    and len(test.ops) == len(test.comparators) == 1
                    and isinstance(test.ops[0], ast.In)
                    and isinstance(test.comparators[0], ast.Name)
                    and test.comparators[0].id == 'services'
                    and not node.orelse and isinstance(call, ast.Call)
                    and isinstance(call.func, ast.Attribute)
                    and isinstance(call.func.value, ast.Name)
                    and call.func.value.id == 'staged' and call.func.attr == 'add'
                    and len(call.args) == 3 and not call.keywords
                    and isinstance(call.args[0], ast.Constant) and call.args[0].value == 'GET'
                    and isinstance(call.args[1], ast.Constant) and call.args[1].value == permitted[key]
                )
                if not allowed:
                    raise AuditError(f'Conditional route registration needs explicit support: {relative}:{node.lineno}')
                walk(call, environment.copy(), owner)
                return
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
                metadata_optional = relative == 'python/artex/server/intercept_read_setup.py' and owner == '_InterceptReadApplication.__init__'
                optional = owner.endswith('.register') or metadata_optional
                dependencies = 'Injected auth store, password codec, signing key; deployer-supplied transport/listener'
                if metadata_optional:
                    dependencies = 'Explicit typed metadata service selection; original JWT application, same executor and independent global/source/raw disclosure grants; no default activation'
                elif owner == 'TaskCategoryService.register':
                    dependencies = ('Explicit TaskCategoryService.register(router) call; injected category repository for list/create; '
                                    'live task manager required for rename/delete/task assignment; missing adapters return 503')
                elif owner == 'TaskService.register':
                    dependencies = ('build_application explicitly registers TaskService; injected manager/metrics/profile resolver required; '
                                    'create/resume also require cooperative runner and graph bootstrap; missing adapters return 503')
                elif owner == 'LogService.register':
                    dependencies = ('build_application explicitly registers LogService; authenticated history requires LogStore/executor; '
                                    'snapshot requires explicitly supplied log ring; no SSE/capture wiring')
                elif owner == 'AssetService.register':
                    dependencies = ('Explicit asset store configuration/global-read opt-in; supported upserts require transaction, '
                                    'company resolver, mutation guard and hostname public-suffix adapter; DELETE is unsupported 503')
                elif owner == 'FindingService.register':
                    dependencies = ('Explicit finding store and asset-label resolver; metadata-only PATCH; status notifications, '
                                    'exports, retention/deletion and retests are unavailable 503 contracts')
                routes.append({'method': method.upper(), 'path': path,
                               'source': relative, 'line': node.lineno, 'function': owner,
                               'handler': ast.unparse(node.args[2]), 'public': public,
                               'registration_mode': 'optional explicit service registration' if optional else 'auth router factory',
                               'dependencies': dependencies,
                               'status': ('optional offline contract; injected repository/live manager required'
                                          if optional else 'offline contract only; production integration unverified')})
                return
            if isinstance(node, ast.Call):
                invalidate_calls(node, environment)
            for child in ast.iter_child_nodes(node):
                walk(child, environment, owner)

        walk(tree, {})
    return routes, hashes


# Audited alternative deployments, not duplicate registrations on one router.
# Every declaration must match its exact source AND owner; another declaration
# (including a second occurrence at the same owner) still fails the audit.
_ALTERNATIVE_BASE = 'python/artex/server/'
PYTHON_ALTERNATIVE_GROUPS = {
    ('GET','/api/tasks/{id}/coverage'): ('explicit-coverage-service-or-dynamic-read', (('task_coverage.py','TaskCoverageService.register'),('task_runtime_read_router.py','TaskRuntimeReadRouter.bind'))),
    ('GET','/api/tasks/{id}/coverage-graph'): ('explicit-coverage-service-or-dynamic-read', (('task_coverage.py','TaskCoverageService.register'),('task_runtime_read_router.py','TaskRuntimeReadRouter.bind'))),
    ('GET','/api/tasks/{id}/asset-refs'): ('explicit-coverage-service-or-dynamic-read', (('task_coverage.py','TaskCoverageService.register'),('task_runtime_read_router.py','TaskRuntimeReadRouter.bind'))),

    ('GET','/api/tasks/{id}/goals'): ('explicit-goal-crud-or-dynamic-read-only', (('task_goals.py','TaskGoalService.register'),('task_runtime_read_router.py','TaskRuntimeReadRouter.bind'))),
    ('GET','/api/exploration/main-sessions'): ('explicit-fixed-or-dynamic-main-session-read', (('main_sessions.py','MainSessionsService.register'),('task_runtime_read_router.py','TaskRuntimeReadRouter.bind'))),
    ('GET','/api/tasks/{id}'): ('explicit-fixed-or-dynamic-task-read-overlay', (('tasks.py','TaskService.register'),('task_runtime_reads.py','TaskRuntimeReads.bind'),('task_runtime_read_router.py','TaskRuntimeReadRouter.bind'))),
    ('GET','/api/stats'): ('explicit-fixed-or-dynamic-task-read-overlay', (('stats.py','StatsService.register'),('task_runtime_reads.py','TaskRuntimeReads.bind'),('task_runtime_read_router.py','TaskRuntimeReadRouter.bind'))),
    ('GET','/api/exploration/intents'): ('explicit-fixed-or-dynamic-task-read-overlay', (('exploration.py','ExplorationService.register'),('task_runtime_reads.py','TaskRuntimeReads.bind'),('task_runtime_read_router.py','TaskRuntimeReadRouter.bind'))),
    ('GET','/api/llm/profiles'): ('explicit-fixed-or-dynamic-task-read-overlay', (('profiles.py','ProfileService.register'),('task_runtime_reads.py','TaskRuntimeReads.bind'),('task_runtime_read_router.py','TaskRuntimeReadRouter.bind'))),
    ('GET','/api/exploration/findings'): ('explicit-task-findings-or-global-and-read-overlays', (('findings.py','FindingService.register'),('task_findings.py','TaskFindingsService.register'),('task_runtime_reads.py','TaskRuntimeReads.bind'),('task_runtime_read_router.py','TaskRuntimeReadRouter.bind'))),
    ('GET','/api/exploration/activity'): ('explicit-fixed-or-dynamic-activity-read', (('task_activity.py','TaskActivityService.register'),('task_runtime_read_router.py','TaskRuntimeReadRouter.bind'))),
    ('GET','/api/exploration/activity/history'): ('explicit-fixed-or-dynamic-activity-read', (('task_activity.py','TaskActivityService.register'),('task_runtime_read_router.py','TaskRuntimeReadRouter.bind'))),
    ('GET','/api/exploration/activity/stream'): ('explicit-fixed-or-dynamic-activity-read', (('task_activity.py','TaskActivityService.register'),('task_runtime_read_router.py','TaskRuntimeReadRouter.bind'))),
    ('GET','/api/exploration/activity/{seq}'): ('explicit-fixed-or-dynamic-activity-read', (('task_activity.py','TaskActivityService.register'),('task_runtime_read_router.py','TaskRuntimeReadRouter.bind'))),
}


def group_python_routes(declarations):
    groups = {}
    for route in declarations:
        groups.setdefault((route['method'],route['path']), []).append(route)
    result=[]
    for key, members in groups.items():
        if len(members)==1:
            result.append(dict(members[0],declarations=list(members)))
            continue
        spec=PYTHON_ALTERNATIVE_GROUPS.get(key)
        actual=Counter((r['source'],r['function']) for r in members)
        expected=Counter((_ALTERNATIVE_BASE+source,owner) for source,owner in spec[1]) if spec else None
        if expected is None or actual!=expected or any(r['public'] is not False for r in members):
            raise AuditError(f'Unexpected duplicate Python route declarations: {key[0]} {key[1]}')
        result.append(dict(members[0],declarations=list(members),alternative_group=spec[0],
            alternative_note='Audited alternate composition only; never permission to mount duplicate handlers on one router.'))
    return result


def inventory(root: Path) -> dict:
    go, mounts, go_hashes, registration_calls = go_inventory(root)
    declarations, python_hashes = python_inventory(root)
    python = group_python_routes(declarations)
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
    return {'schema_version': 2,
            'scope': 'Static registration coverage only, not HTTP service availability or behavior parity.',
            'summary': {'go_registration_call_sites': registration_calls,
                        'go_method_path_routes': len(go), 'go_mount_handlers': len(mounts),
                        'python_offline_contract_routes': len(python),
                        'python_route_declarations': len(declarations),
                        'python_alternative_route_groups': sum('alternative_group' in r for r in python),
                        'python_optional_service_routes': sum(r['registration_mode'].startswith('optional') for r in python),
                        'matched_offline_contract_routes': sum(r['python'] is not None for r in go),
                        'go_routes_without_python_registration': sum(r['python'] is None for r in go)},
            'source_sha256': {**go_hashes, **python_hashes},
            'go_routes': go, 'go_mounts': mounts, 'python_routes': python,
            'python_route_declarations': declarations,
            'python_only': [r for r in python if (r['method'], r['path']) not in go_keys]}


def markdown(data: dict) -> str:
    summary = data['summary']
    lines = ['# Generated route coverage inventory', '',
             'Generated by `python python/tools/route_inventory.py`. This reads source only:',
             'it does not import the application, open a listener, contact a database, or call tools.', '',
             '**Registration is not behavioral parity.** The Python matches below are offline',
             'contracts with injected adapters, not deployed HTTP endpoints. The callable WSGI',
             'adapter does not start a listener or prove end-to-end service reachability.',
             'The application factory explicitly registers category, task and log services.',
             'Category mutations require a live manager; tasks require metrics/runtime adapters;',
             'log snapshots require a capture ring. Missing required adapters return 503.',
             'Assets require explicit scope/global-read configuration and mutation adapters.',
             'DELETE /api/assets is registered only to return unsupported 503; it is not a completed operation.',
             'Declarations do not prove execution readiness. Production limitations are in',
             '`artex/server/PORT_STATUS.md`. No route is labeled production-ready.', '',
             '## Counts', '',
             'Unique method/path counts collapse only exact audited alternative declarations.',
             'Declaration counts retain every source location; alternatives are not simultaneous mounts.', '']
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
        status = (f'{"Optional registration; adapters required" if counterpart["registration_mode"].startswith("optional") else "Offline contract only"}: `{counterpart["source"]}:{counterpart["line"]}`'
                  if counterpart else 'Not registered')
        if counterpart and 'alternative_group' in counterpart:
            status += ' (audited alternatives: ' + ', '.join(f"`{r['source']}:{r['line']}`" for r in counterpart['declarations']) + ')'
        lines.append(f'| {route["method"]} | `{route["path"]}` | `{route["source"]}:{route["line"]}` | {status} |')
    lines += ['', '## Python registration prerequisites', '']
    for route in data['python_routes']:
        for declaration in route['declarations']:
            lines.append(f'- `{route["method"]} {route["path"]}` at `{declaration["source"]}:{declaration["line"]}` ({declaration["function"]}): {declaration["dependencies"]}.')
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
