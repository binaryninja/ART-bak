"""Exploration graph persistence, bound to one exploration at construction.

Implements roots, node/edge reads/writes, anchors, goal payloads, intent CAS,
frontier, stats and direct read-only inheritance. Finding evidence integration,
digest compaction, cancellation cleanup, soft/hard deletion and execution fences
are not ported. Callers must enforce task/worker lifetime barriers before writes.
Additional isolation versus upstream: edge endpoints and anchor nodes must belong
to this exploration; inherited handles reject writes. Node payload/state changes
invalidate digest content versions. Contract tests are not live PostgreSQL proof.
"""
from __future__ import annotations
from dataclasses import dataclass, field, asdict
from datetime import datetime
import json
from .core import Query, Executor, int64

STATES = {
    'begin': ('open',), 'goal': ('open', 'met', 'abandoned'),
    'intent': ('open', 'running', 'paused', 'done', 'blocked', 'exhausted', 'stopped', 'deleted'),
    'fact': ('confirmed', 'dismissed', 'origin'), 'finding': ('confirmed', 'dismissed'),
    'hint': ('active', 'consumed'), 'digest': ('active', 'superseded'),
}
RELATIONS = ('spawns', 'derived_from', 'yields', 'proves', 'covers')
NODE_COLS = "id, kind, payload, priority, state, COALESCE(origin,''), COALESCE(owner,''), COALESCE(blocked_reason,''), COALESCE(delete_reason,''), created_at"


class GraphInvalid(ValueError):
    pass


class GraphViewOverflow(RuntimeError):
    pass


class GraphNotFound(LookupError):
    pass


class InheritedWriteError(PermissionError):
    pass


@dataclass
class Node:
    id: int
    kind: str
    payload: object
    priority: int
    state: str
    origin: str
    owner: str
    blocked_reason: str
    delete_reason: str
    created_at: datetime
    anchors: list[int] = field(default_factory=list)
    source_task_id: int = 0
    inherited: bool = False
    finding_id: int = 0
    finding_node_id: int = 0
    traffic_count: int = 0

    def __post_init__(self):
        if isinstance(self.payload, (str, bytes)):
            self.payload = json.loads(self.payload)

    def to_dict(self):
        values = asdict(self)
        values['created_at'] = self.created_at.isoformat()
        for key in ('origin', 'owner', 'blocked_reason', 'delete_reason', 'anchors',
                    'source_task_id', 'inherited', 'finding_id', 'finding_node_id', 'traffic_count'):
            if not values[key]:
                del values[key]
        return values


@dataclass(frozen=True)
class Edge:
    from_id: int
    rel: str
    to_id: int

    def to_dict(self):
        return {'From': self.from_id, 'Rel': self.rel, 'To': self.to_id}


@dataclass(frozen=True)
class DirectSource:
    task_id: int
    exploration_id: int
    description: str
    goal: str
    status: str
    store: ExplorationStore


def _kind(kind):
    if kind not in STATES:
        raise GraphInvalid('invalid exploration node kind')


def _state(kind, state):
    _kind(kind)
    if state not in STATES[kind]:
        raise GraphInvalid(f'invalid {kind} state')


def _id(value, name='node id'):
    value = int64(value, name)
    if value <= 0:
        raise GraphInvalid(f'{name} must be positive')
    return value


def _limit(value, default):
    value = int64(value, 'limit')
    return value if value > 0 else default


def _json(payload):
    if not isinstance(payload, dict):
        raise GraphInvalid('payload must be an object')
    # Remove actual NULs, not literal backslash-u text; validate JSON beforehand.
    def clean(value):
        if isinstance(value, str):
            return value.replace('\x00', '')
        if isinstance(value, dict):
            if not all(isinstance(key, str) for key in value):
                raise GraphInvalid('JSON object keys must be strings')
            return {clean(key): clean(item) for key, item in value.items()}
        if isinstance(value, list):
            return [clean(item) for item in value]
        return value
    try:
        result = json.dumps(clean(payload), ensure_ascii=False, allow_nan=False)
        result.encode('utf-8')  # Reject invalid surrogates instead of sending bad UTF-8.
        return result
    except (TypeError, ValueError, UnicodeError) as error:
        raise GraphInvalid('payload must be valid JSON and UTF-8') from error


def create_exploration(executor: Executor, description: str, goal: str) -> int:
    if not isinstance(description, str) or not isinstance(goal, str):
        raise GraphInvalid('description and goal must be strings')
    return executor.fetch_one(Query('INSERT INTO explorations(description, goal) VALUES ($1, $2) RETURNING id', (description, goal)))[0]


class ExplorationStore:
    def __init__(self, executor: Executor, exploration_id: int, *, read_only=False, source_task_id=0):
        self.executor = executor
        self.exploration_id = _id(exploration_id, 'exploration id')
        self.read_only = read_only
        self.source_task_id = source_task_id

    def _write(self):
        if self.read_only:
            raise InheritedWriteError('inherited exploration is read-only')

    def _node(self, row):
        node = Node(*row)
        if self.read_only:
            node.inherited, node.source_task_id = True, self.source_task_id
        return node

    def root(self):
        row = self.executor.fetch_one(Query('SELECT description, goal FROM explorations WHERE id=$1', (self.exploration_id,)))
        return row[0] or '', row[1]

    def task_id(self):
        rows = self.executor.fetch_all(Query('SELECT id FROM tasks WHERE exploration_id=$1 AND deleted_at IS NULL', (self.exploration_id,)))
        return rows[0][0] if rows else 0

    def direct_sources(self):
        if self.read_only:
            return []  # Never expand sources of a source.
        rows = self.executor.fetch_all(Query('''SELECT source.id, source.exploration_id, source.description, source.goal, source.status
FROM tasks owner JOIN task_relations relation ON relation.task_id=owner.id
JOIN tasks source ON source.id=relation.source_task_id AND source.deleted_at IS NULL
WHERE owner.exploration_id=$1 AND owner.deleted_at IS NULL ORDER BY relation.created_at, source.id''', (self.exploration_id,)))
        return [DirectSource(*row, ExplorationStore(self.executor, row[1], read_only=True, source_task_id=row[0])) for row in rows]

    def add_node(self, kind, payload, priority, state, origin='', anchors=()):
        self._write()
        _state(kind, state)
        priority = int64(priority, 'priority')
        if not -(2**31) <= priority < 2**31:
            raise GraphInvalid('priority must fit PostgreSQL INTEGER')
        if not isinstance(origin, str):
            raise GraphInvalid('origin must be a string')
        raw = _json(payload)
        assets = list(dict.fromkeys(_id(asset, 'asset id') for asset in anchors))
        transaction = getattr(self.executor, 'transaction', None)
        if transaction is None:
            raise TypeError('adding nodes requires a transaction-capable executor')
        with transaction() as tx:
            node_id = tx.fetch_one(Query('''INSERT INTO exploration_nodes(exploration_id, kind, payload, priority, state, origin)
VALUES ($1,$2,$3,$4,$5,$6) RETURNING id''', (self.exploration_id, kind, raw, priority, state, origin)))[0]
            for asset in assets:
                tx.execute(Query('INSERT INTO exploration_anchors(node_id, asset_id) VALUES ($1,$2) ON CONFLICT DO NOTHING', (node_id, asset)))
        return node_id

    def add_intent(self, payload, priority=0, anchors=(), origin=''):
        return self.add_node('intent', payload, priority, 'open', origin or 'planner', anchors)

    def add_goal(self, payload, origin=''):
        return self.add_node('goal', payload, 0, 'open', origin)

    def origin_fact_id(self):
        rows = self.executor.fetch_all(Query("SELECT id FROM exploration_nodes WHERE exploration_id=$1 AND kind='fact' AND state='origin' ORDER BY id LIMIT 1", (self.exploration_id,)))
        return rows[0][0] if rows else 0

    def anchor(self, node_id, asset_id):
        self._write()
        # A scoped INSERT SELECT excludes foreign exploration nodes. Zero can
        # also mean a duplicate, deliberately retaining idempotent semantics.
        return self.executor.execute(Query('''INSERT INTO exploration_anchors(node_id, asset_id)
SELECT id, $2 FROM exploration_nodes WHERE id=$1 AND exploration_id=$3
ON CONFLICT DO NOTHING''', (_id(node_id), _id(asset_id, 'asset id'), self.exploration_id))) > 0

    def link(self, from_id, relation, to_id):
        self._write()
        if relation not in RELATIONS:
            raise GraphInvalid('invalid exploration edge relation')
        from_id, to_id = _id(from_id), _id(to_id)
        if from_id == to_id:
            raise GraphInvalid('self edges are forbidden')
        return self.executor.execute(Query('''INSERT INTO exploration_edges(exploration_id, src_id, rel, dst_id)
SELECT $1, src.id, $3, dst.id FROM exploration_nodes src CROSS JOIN exploration_nodes dst
WHERE src.id=$2 AND dst.id=$4 AND src.exploration_id=$1 AND dst.exploration_id=$1
ON CONFLICT (exploration_id, src_id, rel, dst_id) DO NOTHING''', (self.exploration_id, from_id, relation, to_id))) > 0

    def get_node(self, node_id):
        rows = self.executor.fetch_all(Query(f'SELECT {NODE_COLS} FROM exploration_nodes WHERE id=$1 AND exploration_id=$2', (_id(node_id), self.exploration_id)))
        return self._node(rows[0]) if rows else None

    def nodes(self, limit=2000):
        return [self._node(row) for row in self.executor.fetch_all(Query(f'SELECT {NODE_COLS} FROM exploration_nodes WHERE exploration_id=$1 ORDER BY id LIMIT $2', (self.exploration_id, _limit(limit, 2000))))]

    def list_by_kind(self, kind, limit=500):
        _kind(kind)
        return [self._node(row) for row in self.executor.fetch_all(Query(f'SELECT {NODE_COLS} FROM exploration_nodes WHERE exploration_id=$1 AND kind=$2 ORDER BY id DESC LIMIT $3', (self.exploration_id, kind, _limit(limit, 500))))]

    def get_goal_bounded(self, node_id, *, max_payload_bytes=65536, max_origin_bytes=128):
        """Scoped goal DTO only; oversized fields become SQL NULL sentinels.

        Unused node metadata is never transferred. This optional HTTP read leaves
        generic graph reads unchanged and returns None only for a missing goal.
        """
        node_id=_id(node_id)
        for value,cap in ((max_payload_bytes,1048576),(max_origin_bytes,65536)):
            if type(value) is not int or not 0<value<=cap:
                raise GraphInvalid('Positive bounded goal read limits required')
        rows=self.executor.fetch_all(Query("""SELECT id,'goal',
CASE WHEN octet_length(payload::text)<=$3 THEN payload ELSE NULL END,0,
CASE WHEN octet_length(state)<=32 THEN state ELSE NULL END,
CASE WHEN octet_length(COALESCE(origin,''))<=$4 THEN COALESCE(origin,'') ELSE NULL END,
'','','',created_at FROM exploration_nodes
WHERE id=$1 AND exploration_id=$2 AND kind='goal' /* bounded_goal */""",
            (node_id,self.exploration_id,max_payload_bytes,max_origin_bytes)))
        if not rows:return None
        if len(rows)!=1 or len(rows[0])!=10:raise GraphViewOverflow('Invalid goal view')
        row=rows[0]
        if row[0]!=node_id or row[1]!='goal' or row[2] is None or not isinstance(row[4],str) or not isinstance(row[5],str):
            raise GraphViewOverflow('Goal view exceeds bounds')
        payload=row[2] if isinstance(row[2],str) else json.dumps(row[2],ensure_ascii=False,allow_nan=False)
        if len(payload.encode('utf-8'))>max_payload_bytes or len(row[4].encode('utf-8'))>32 or len(row[5].encode('utf-8'))>max_origin_bytes:
            raise GraphViewOverflow('Goal view exceeds bounds')
        return self._node(row)

    def list_goals_bounded(self, *, max_rows=10000, max_payload_bytes=65536,
                           max_origin_bytes=128, max_total_bytes=1048576):
        """Complete goal DTO inputs with server-side byte/row overflow sentinels.

        Existing generic reads are unchanged. Extra node fields not used by the
        goal DTO are not fetched. Window totals precede LIMIT in one snapshot;
        no oversized JSON/text is transferred before the HTTP response bound.
        """
        for value, cap in ((max_rows,10000),(max_payload_bytes,1048576),
                           (max_origin_bytes,65536),(max_total_bytes,16777216)):
            if type(value) is not int or not 0<value<=cap:
                raise GraphInvalid('Positive bounded goal read limits required')
        rows=self.executor.fetch_all(Query("""SELECT id,'goal',
CASE WHEN total_count<=$3 AND total_bytes<=$6 AND octet_length(payload::text)<=$4
          AND octet_length(origin)<=$5 AND octet_length(state)<=32 THEN payload ELSE NULL END,
0,
CASE WHEN octet_length(state)<=32 THEN state ELSE NULL END,
CASE WHEN total_count<=$3 AND total_bytes<=$6 AND octet_length(origin)<=$5 THEN origin ELSE NULL END,
'','','',created_at,total_count
FROM (SELECT id,payload,state,COALESCE(origin,'') AS origin,created_at,
COUNT(*) OVER () AS total_count,
SUM(octet_length(payload::text)+octet_length(COALESCE(origin,''))+octet_length(state)) OVER () AS total_bytes
FROM exploration_nodes WHERE exploration_id=$1 AND kind='goal'
ORDER BY id DESC LIMIT $2) AS bounded_goals
ORDER BY id DESC""",(self.exploration_id,max_rows+1,max_rows,max_payload_bytes,max_origin_bytes,max_total_bytes)))
        if len(rows)>max_rows:raise GraphViewOverflow('Goal view exceeds bounds')
        result=[];size=0
        for row in rows:
            if len(row)!=11 or type(row[10]) is not int or row[10]!=len(rows) or row[10]>max_rows:
                raise GraphViewOverflow('Incomplete goal view')
            if row[2] is None or not isinstance(row[4],str) or not isinstance(row[5],str):
                raise GraphViewOverflow('Goal view exceeds bounds')
            payload=row[2] if isinstance(row[2],str) else json.dumps(row[2],ensure_ascii=False,allow_nan=False)
            payload_size=len(payload.encode('utf-8'));origin_size=len(row[5].encode('utf-8'));state_size=len(row[4].encode('utf-8'))
            size+=payload_size+origin_size+state_size
            if payload_size>max_payload_bytes or origin_size>max_origin_bytes or state_size>32 or size>max_total_bytes:
                raise GraphViewOverflow('Goal view exceeds bounds')
            result.append(self._node(row[:10]))
        return result

    def list_by_kind_page(self, kind, before=0, limit=300):
        _kind(kind)
        limit = _limit(limit, 300)
        rows = self.executor.fetch_all(Query(f'''SELECT {NODE_COLS} FROM exploration_nodes
WHERE exploration_id=$1 AND kind=$2 AND ($3 <= 0 OR id < $3) ORDER BY id DESC LIMIT $4''', (self.exploration_id, kind, int64(before, 'before'), limit+1)))
        return [self._node(row) for row in rows[:limit]], len(rows) > limit

    def edges(self, limit=5000):
        return [Edge(*row) for row in self.executor.fetch_all(Query('SELECT src_id, rel, dst_id FROM exploration_edges WHERE exploration_id=$1 LIMIT $2', (self.exploration_id, _limit(limit, 5000))))]

    def update_goal_payload(self, node_id, text, vulnclass=''):
        self._write()
        if not isinstance(text, str) or not isinstance(vulnclass, str):
            raise GraphInvalid('goal text and class must be strings')
        payload = {'text': text}
        if vulnclass:
            payload['vulnclass'] = vulnclass
        count = self.executor.execute(Query("UPDATE exploration_nodes SET payload=$1, content_version=content_version+1 WHERE id=$2 AND exploration_id=$3 AND kind='goal'", (_json(payload), _id(node_id), self.exploration_id)))
        if not count:
            raise GraphNotFound('goal does not exist in this exploration')

    def update_payload(self, node_id, payload):
        self._write()
        count = self.executor.execute(Query('UPDATE exploration_nodes SET payload=$1, content_version=content_version+1 WHERE id=$2 AND exploration_id=$3', (_json(payload), _id(node_id), self.exploration_id)))
        if not count:
            raise GraphNotFound('node does not exist in this exploration')

    def set_node_state(self, node_id, state):
        self._write()
        kinds = [kind for kind, states in STATES.items() if state in states]
        if not kinds:
            raise GraphInvalid('invalid node state')
        count = self.executor.execute(Query('''UPDATE exploration_nodes SET state=$1, blocked_reason=NULL, content_version=content_version+1
WHERE id=$2 AND exploration_id=$3 AND kind=ANY($4::text[])''', (state, _id(node_id), self.exploration_id, kinds)))
        if not count:
            raise GraphNotFound('node missing from exploration or state incompatible with kind')

    def set_intent_state(self, node_id, state):
        self._write()
        _state('intent', state)
        return self.executor.execute(Query('''UPDATE exploration_nodes SET state=$1, blocked_reason=NULL, content_version=content_version+1,
completed_at=CASE WHEN $4 THEN now() ELSE NULL END WHERE id=$2 AND exploration_id=$3 AND kind='intent' ''',
            (state, _id(node_id), self.exploration_id, state in ('done','blocked','exhausted','stopped')))) > 0

    def compare_and_set_intent_state(self, node_id, expected, state):
        self._write()
        _state('intent', expected)
        _state('intent', state)
        return self.executor.execute(Query('''UPDATE exploration_nodes SET state=$1, blocked_reason=NULL, content_version=content_version+1,
completed_at=CASE WHEN $5 THEN now() ELSE NULL END
WHERE id=$2 AND exploration_id=$3 AND kind='intent' AND state=$4''',
            (state, _id(node_id), self.exploration_id, expected, state in ('done','blocked','exhausted','stopped')))) == 1

    def claim_intent(self, node_id, owner):
        self._write()
        if not isinstance(owner, str):
            raise GraphInvalid('owner must be a string')
        return self.executor.execute(Query("UPDATE exploration_nodes SET state='running', owner=$1 WHERE id=$2 AND exploration_id=$3 AND kind='intent' AND state='open'", (owner, _id(node_id), self.exploration_id))) == 1

    def frontier(self, limit=50):
        return [self._node(row) for row in self.executor.fetch_all(Query(f"SELECT {NODE_COLS} FROM exploration_nodes WHERE exploration_id=$1 AND kind='intent' AND state='open' ORDER BY priority DESC, id ASC LIMIT $2", (self.exploration_id, _limit(limit, 50))))]

    def has_active_intent(self):
        return self.executor.fetch_one(Query("SELECT EXISTS(SELECT 1 FROM exploration_nodes WHERE exploration_id=$1 AND kind='intent' AND state IN ('open','running'))", (self.exploration_id,)))[0]

    def has_open_goal(self):
        return self.executor.fetch_one(Query("SELECT EXISTS(SELECT 1 FROM exploration_nodes WHERE exploration_id=$1 AND kind='goal' AND state='open')", (self.exploration_id,)))[0]

    def stats(self):
        return dict(self.executor.fetch_all(Query('SELECT kind, count(*) FROM exploration_nodes WHERE exploration_id=$1 GROUP BY kind', (self.exploration_id,))))

    def delete_goal(self, node_id):
        """Source goal-only deletion; caller owns idle/lifetime and delete authority.

        Schema cascades remove this goal's edges/anchors, not their other nodes.
        Generic graph/worker deletion remains unavailable through delete_node.
        """
        self._write()
        affected = self.executor.execute(Query(
            "DELETE FROM exploration_nodes WHERE id=$1 AND exploration_id=$2 AND kind='goal'",
            (_id(node_id), self.exploration_id)))
        if type(affected) is not int or affected not in (0, 1):
            raise GraphInvalid('goal deletion count not confirmed')
        if affected == 0:
            raise GraphNotFound('goal does not exist in this exploration')

    def delete_node(self, node_id):
        self._write()
        raise NotImplementedError('graph deletion requires unported worker fences and cleanup paths')
