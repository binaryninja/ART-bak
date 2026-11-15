"""Offline asset DSL parser and bound-parameter SQL compiler.

Port of db/asset_dsl.go's parsing/WHERE helpers only. No database access or task
scope expansion is implemented. Wrap the returned WHERE expression in parentheses
before adding an AND scope/type constraint, so an OR cannot escape that constraint.
Unknown field-like tokens remain bare-text searches, as in the Go implementation.
Unicode whitespace is recognized correctly rather than using Go's byte-wise test.
"""
from dataclasses import dataclass
import re

_STRING = {name: name for name in (
    'domain', 'root_domain', 'ip', 'url', 'page_title', 'icp', 'service_name',
    'app_name', 'bundle_id', 'category', 'app_icp', 'method', 'service_type', 'record_type')}
_STRING['title'] = 'page_title'
_ARRAY = dict.fromkeys(('technology', 'technologies', 'tech'), 'technologies')
_NUMERIC = {'port': 'port', 'status_code': 'status_code', 'status': 'status_code'}
_KNOWN = set(_STRING) | set(_ARRAY) | set(_NUMERIC) | {'company_id', 'task_id'}
_TEXT = ('domain', 'root_domain', 'ip', 'url', 'page_title', 'icp', 'service_name', 'app_name', 'app_description')
_FIELD = re.compile(r'([a-zA-Z_][a-zA-Z_0-9]*)(==|!=|>=|<=|>|<|=)')


class DSLError(ValueError):
    """A syntax or field/operator/value validation error, not a database error."""


@dataclass(frozen=True)
class Expr:
    field: str
    op: str
    value: str


@dataclass(frozen=True)
class Node:
    kind: str
    children: tuple['Node', ...] = ()
    expr: Expr | None = None


def _read_token(text: str, pos: int) -> tuple[str, int]:
    if pos == len(text):
        return '', pos
    if text[pos] == '"':
        end = text.find('"', pos + 1)
        # Upstream deliberately permits an unterminated quoted token.
        return (text[pos + 1:], len(text)) if end < 0 else (text[pos + 1:end], end + 1)
    end = pos
    while end < len(text) and not text[end].isspace() and text[end] not in '()':
        end += 1
    return text[pos:end], end


def _tokenize(text: str) -> list[tuple[str, Expr | None]]:
    tokens = []
    pos = 0
    while pos < len(text):
        if text[pos].isspace():
            pos += 1
            continue
        if text[pos] in '()':
            tokens.append((text[pos], None))
            pos += 1
            continue
        match = _FIELD.match(text, pos)
        if match and match[1].lower() in _KNOWN:
            value, end = _read_token(text, match.end())
            if end != match.end():
                tokens.append(('LEAF', Expr(match[1].lower(), match[2], value)))
                pos = end
                continue
        value, end = _read_token(text, pos)
        pos = max(end, pos + 1)
        if value:
            tokens.append((value.upper(), None) if value.upper() in ('AND', 'OR')
                          else ('LEAF', Expr('', '=', value)))
    tokens.append(('EOF', None))
    return tokens


def parse_dsl(text: str) -> Node | None:
    """Parse explicit AND/OR with parentheses; AND binds more tightly than OR."""
    if not text.strip():
        return None
    tokens = _tokenize(text)
    pos = 0

    def atom() -> Node:
        nonlocal pos
        kind, expr = tokens[pos]
        if kind == 'LEAF':
            pos += 1
            return Node('leaf', expr=expr)
        if kind == '(':
            pos += 1
            node = combine('OR')
            if tokens[pos][0] != ')':
                raise DSLError("DSL syntax error: missing closing ')'")
            pos += 1
            return node
        raise DSLError(f'DSL syntax error: unexpected {kind}')

    def combine(operator: str) -> Node:
        nonlocal pos
        def child():
            return atom() if operator == 'AND' else combine('AND')
        nodes = [child()]
        while tokens[pos][0] == operator:
            pos += 1
            nodes.append(child())
        return nodes[0] if len(nodes) == 1 else Node(operator.lower(), tuple(nodes))

    try:
        root = combine('OR')
    except RecursionError as exc:
        raise DSLError('DSL nesting exceeds parser capacity') from exc
    if tokens[pos][0] != 'EOF':
        raise DSLError(f'DSL syntax error: unexpected content {tokens[pos][0]}')
    return root


def _integer(value: str, field: str) -> int:
    # Python int otherwise accepts whitespace, underscores and non-ASCII digits.
    if not re.fullmatch(r'[+-]?[0-9]+', value):
        raise DSLError(f'Field {field} requires an integer: {value}')
    digits = value.lstrip('+-').lstrip('0') or '0'
    if len(digits) > 19:
        raise DSLError(f'Field {field} requires a signed 64-bit integer')
    number = int(('-' if value.startswith('-') else '') + digits)
    if not -(2**63) <= number < 2**63:
        raise DSLError(f'Field {field} requires a signed 64-bit integer')
    return number


def build_dsl_where(node: Node | None, base: int = 0) -> tuple[str, list[str | int]]:
    """Compile an AST to PostgreSQL $N placeholders plus separate bound values.

    base reserves existing parameter positions. This does not execute SQL, expand
    declared task scope, hydrate results or implement QueryDSL/CountDSL.
    """
    if type(base) is not int or base < 0:
        raise DSLError('Placeholder base must be a non-negative integer')
    args: list[str | int] = []

    def bind(value: str | int) -> str:
        args.append(value)
        return f'${base + len(args)}'

    def leaf(expr: Expr) -> str:
        field, op, value = expr.field.lower(), expr.op, expr.value
        if not field:
            param = bind('%' + value + '%')
            parts = [f'{column} ILIKE {param}' for column in _TEXT]
            parts += [f'EXISTS (SELECT 1 FROM unnest({column}) t(v) WHERE v ILIKE {param})'
                      for column in ('technologies', 'bound_domains')]
            return '(' + ' OR '.join(parts) + ')'
        if field in ('task_id', 'company_id'):
            param = bind(_integer(value, field))
            # Upstream ignores the requested operator for these two fields.
            return f'{param} = ANY(task_ids)' if field == 'task_id' else f'company_id = {param}'
        if field in _NUMERIC:
            number = _integer(value, field)
            op = '=' if op == '==' else op
            if op not in ('=', '!=', '>', '>=', '<', '<='):
                raise DSLError(f'Field {field} does not support operator {op}')
            return f'{_NUMERIC[field]} {op} {bind(number)}'
        if field in _ARRAY:
            column = _ARRAY[field]
            if op in ('==', '!='):
                clause = f'{bind(value)} = ANY({column})'
                return f'NOT ({clause})' if op == '!=' else clause
            if op == '=':
                return f'EXISTS (SELECT 1 FROM unnest({column}) t(v) WHERE v ILIKE {bind("%" + value + "%")})'
        elif field in _STRING:
            column = _STRING[field]
            if op == '==':
                return f'{column} = {bind(value)}'
            if op in ('=', '!='):
                return f'{column} {"ILIKE" if op == "=" else "NOT ILIKE"} {bind("%" + value + "%")}'
        else:
            raise DSLError(f'Unknown field: {field}')
        raise DSLError(f'Field {field} does not support operator {op}')

    def build(item: Node) -> str:
        if item.kind in ('and', 'or') and item.children:
            return f' {item.kind.upper()} '.join(f'({build(c)})' for c in item.children)
        if item.kind == 'leaf' and item.expr is not None:
            return leaf(item.expr)
        raise DSLError(f'Invalid AST node: {item.kind}')

    try:
        return ('1=1' if node is None else build(node)), args
    except RecursionError as exc:
        raise DSLError('DSL nesting exceeds compiler capacity') from exc


def compile_dsl(text: str, base: int = 0) -> tuple[str, list[str | int]]:
    return build_dsl_where(parse_dsl(text), base)


def validate_dsl(text: str) -> None:
    """Raise DSLError for invalid syntax/operations without contacting a database."""
    compile_dsl(text)
