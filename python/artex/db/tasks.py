"""Task registry persistence: read/hydrate, create, metadata and lifecycle flags.

No runner, admission scheduler, tool execution or schema setup lives here. Create
atomically writes exploration/root fact/task, direct-source links, company scope
snapshots and LLM chain. Nonempty intercept_rules are explicitly unsupported.
Hard deletion/archive/traffic cleanup and live LLM-chain replacement are NOT
implemented: their cross-store barriers must be ported before exposing them.
Lifecycle methods persist flags only; callers must coordinate actual workers.
All SQL uses native PostgreSQL bound parameters. Tested with contract fakes, not
live PostgreSQL. List/get hydrate context in three bulk queries, as upstream.
"""
from __future__ import annotations
from dataclasses import dataclass, field, asdict
from datetime import datetime
import json
from .core import Executor, Query, int64
from .task_categories import CategoryInvalid, CategoryNotFound

MIN_PLAN_HEARTBEAT_SECONDS = 600
MAX_TASK_SOURCE_COUNT = 8
MAX_TASK_COMPANY_COUNT = 32
STATUSES = ('created', 'running', 'paused', 'done', 'failed', 'timeout')


class TaskInvalid(ValueError):
    pass


class TaskSourceNotFound(LookupError):
    pass


class TaskCompanyNotFound(LookupError):
    pass


def is_terminal(status: str) -> bool:
    return status in ('done', 'failed', 'timeout')


@dataclass
class Task:
    id: int
    name: str
    category_id: int | None
    category_name: str
    description: str
    goal: str
    exploration_id: int
    status: str
    paused: bool
    queued: bool
    queued_at: datetime | None
    queue_mode: str
    llm_profile_id: int | None
    active_llm_profile_id: int | None
    parent_ref: str
    pinned_at: datetime | None
    created_at: datetime
    completed_at: datetime | None
    timeout_seconds: int
    plan_heartbeat_seconds: int
    coverage_enabled: bool
    first_run_at: datetime | None
    deadline_at: datetime | None
    llm_chain_revision: int = 0
    llm_profile_ids: list[int] = field(default_factory=list)
    source_task_ids: list[int] = field(default_factory=list)
    company_ids: list[int] = field(default_factory=list)
    llm_failover_state: str = 'default'
    llm_failover_reason: str = ''

    @property
    def pinned(self):
        return self.pinned_at is not None

    def to_dict(self):
        result = asdict(self)
        del result['llm_chain_revision']
        result['pinned'] = self.pinned
        optional = ('category_id', 'category_name', 'pinned_at', 'queued_at', 'queue_mode',
                    'llm_profile_id', 'active_llm_profile_id', 'llm_profile_ids',
                    'source_task_ids', 'company_ids', 'parent_ref', 'completed_at',
                    'first_run_at', 'deadline_at', 'llm_failover_state', 'llm_failover_reason')
        for key in optional:
            if result[key] is None or result[key] == '' or result[key] == []:
                del result[key]
        return {key: value.isoformat() if isinstance(value, datetime) else value for key, value in result.items()}


@dataclass
class TaskCreateOptions:
    name: str = ''
    category_id: int | None = None
    source_task_ids: list[int] = field(default_factory=list)
    company_ids: list[int] = field(default_factory=list)
    llm_profile_ids: list[int] = field(default_factory=list)
    timeout_seconds: int = 0
    plan_heartbeat_seconds: int = 0
    coverage_enabled: bool | None = None
    intercept_rules: list = field(default_factory=list)


TASK_COLS = """id, COALESCE(name,''), category_id,
COALESCE((SELECT category.name FROM task_categories category WHERE category.id=tasks.category_id),''),
description, goal, exploration_id, status, paused, queued, queued_at, COALESCE(queue_mode,''),
llm_profile_id, active_llm_profile_id, COALESCE(parent_ref,''), pinned_at, created_at, completed_at,
COALESCE(timeout_seconds,0), COALESCE(plan_heartbeat_seconds,300), COALESCE(coverage_enabled,true),
first_run_at, deadline_at"""


def _ids(values, name, *, deduplicate=False, maximum=None):
    out = []
    seen = set()
    for value in values:
        value = int64(value, name)
        if value <= 0:
            raise TaskInvalid(f'{name} must be positive')
        if value in seen:
            if deduplicate:
                continue
            raise TaskInvalid(f'duplicate {name}: {value}')
        seen.add(value)
        out.append(value)
        if maximum is not None and len(out) > maximum:
            raise TaskInvalid(f'at most {maximum} {name}s are allowed')
    return out


def _row(executor, query):
    rows = executor.fetch_all(query)
    if len(rows) > 1:
        raise RuntimeError('expected at most one task row')
    return Task(*rows[0]) if rows else None


class TaskStore:
    def __init__(self, executor: Executor):
        self.executor = executor

    def replace_llm_profiles_idle(self, task_id, exploration_id, expected_revision, profile_ids, active_profile_id=0):
        """Reset an explicit chain only behind a host idle/config fence.

        Narrow source ReplaceTaskLLMProfiles port: ordered reset, first-ID default,
        revision CAS and task-before-profile lock order. Reject running/paused/
        queued/archived tasks and nonterminal quota-blocked intents: their live
        reconciliation/reopen side effects are not implemented. Caller must sync
        runtime caches/audit after commit; this method does not claim readiness.
        """
        for value in (task_id,exploration_id):
            if int64(value,'task identity')<=0:raise TaskInvalid('Positive task identity required')
        if int64(expected_revision,'chain revision')<0:raise TaskInvalid('Invalid chain revision')
        if not isinstance(profile_ids,(list,tuple)):raise TaskInvalid('Ordered profile IDs required')
        ids=_ids(list(profile_ids),'profile ID',maximum=100)
        if int64(active_profile_id,'active profile ID')<0:raise TaskInvalid('Invalid active profile ID')
        active=(active_profile_id or ids[0]) if ids else None
        if ids and active not in ids:raise TaskInvalid('Active profile not in chain')
        with self.executor.transaction() as tx:
            rows=tx.fetch_all(Query("SELECT status,paused,queued,llm_chain_revision FROM tasks WHERE id=$1 AND exploration_id=$2 AND deleted_at IS NULL AND archived_at IS NULL AND NOT EXISTS (SELECT 1 FROM task_archives WHERE task_id=$1) FOR UPDATE",(task_id,exploration_id)))
            if len(rows)!=1:raise TaskInvalid('Task unavailable')
            status,paused,queued,revision=rows[0]
            if status not in ('created','done','failed','timeout') or paused is not False or queued is not False or revision!=expected_revision:
                raise TaskInvalid('Task lifecycle or chain revision changed')
            if status=='created' and tx.fetch_all(Query("SELECT id FROM exploration_nodes WHERE exploration_id=$1 AND kind='intent' AND state='blocked' AND blocked_reason='llm_quota_exhausted' LIMIT 1",(exploration_id,))):
                raise TaskInvalid('Quota-blocked intent reconciliation unavailable')
            if ids:
                locked=tx.fetch_all(Query("SELECT id FROM llm_profiles WHERE id=ANY($1) AND COALESCE(api_key,'')<>'' ORDER BY id FOR SHARE",(ids,)))
                if sorted(r[0] for r in locked)!=sorted(ids):raise TaskInvalid('Selected profiles unavailable')
            tx.execute(Query('DELETE FROM task_llm_profiles WHERE task_id=$1',(task_id,)))
            for position,profile in enumerate(ids):
                if tx.execute(Query('INSERT INTO task_llm_profiles(task_id,profile_id,position) VALUES ($1,$2,$3)',(task_id,profile,position)))!=1:raise RuntimeError('Chain insert unconfirmed')
            changed=tx.fetch_all(Query('UPDATE tasks SET active_llm_profile_id=$2,llm_profile_id=$2,llm_chain_revision=llm_chain_revision+1 WHERE id=$1 AND llm_chain_revision=$3 RETURNING llm_chain_revision',(task_id,active,expected_revision)))
            if changed!=[(expected_revision+1,)] and changed!=[[expected_revision+1]]:raise RuntimeError('Chain revision update unconfirmed')
            return expected_revision+1

    def _hydrate(self, tasks):
        if not tasks:
            return
        indexed = {task.id: task for task in tasks}
        ids = list(indexed)
        latest = {}
        ready = set()
        rows = self.executor.fetch_all(Query('''SELECT t.id, t.llm_profile_id, t.active_llm_profile_id, t.llm_chain_revision,
       p.profile_id, p.position, p.status, COALESCE(p.last_error,''), p.exhausted_at
FROM tasks t LEFT JOIN task_llm_profiles p ON p.task_id=t.id
WHERE t.id=ANY($1::bigint[]) ORDER BY t.id, p.position NULLS LAST''', (ids,)))
        for task_id, legacy, active, revision, profile, position, status, error, exhausted_at in rows:
            if task_id not in indexed:
                continue
            task = indexed[task_id]
            task.llm_profile_id, task.active_llm_profile_id, task.llm_chain_revision = legacy, active, revision
            if profile is None:
                continue
            task.llm_profile_ids.append(profile)
            if profile == active and status == 'ready':
                ready.add(task_id)
            if exhausted_at is not None and (task_id not in latest or exhausted_at > latest[task_id][0]):
                latest[task_id] = (exhausted_at, error)
        for task in tasks:
            task.llm_failover_state = ('ready' if task.id in ready else 'chain_exhausted') if task.llm_profile_ids else 'default'
            task.llm_failover_reason = latest.get(task.id, (None, ''))[1]
        for task_id, source in self.executor.fetch_all(Query('''SELECT task_id, source_task_id FROM task_relations
WHERE task_id=ANY($1::bigint[]) ORDER BY task_id, created_at, source_task_id''', (ids,))):
            if task_id in indexed:
                indexed[task_id].source_task_ids.append(source)
        for task_id, company in self.executor.fetch_all(Query('''SELECT task_id, company_id FROM task_scope
WHERE task_id=ANY($1::bigint[]) AND kind='company' AND company_id IS NOT NULL ORDER BY task_id, id''', (ids,))):
            if task_id in indexed:
                indexed[task_id].company_ids.append(company)

    def list(self) -> list[Task]:
        tasks = [Task(*row) for row in self.executor.fetch_all(Query(f'''SELECT {TASK_COLS} FROM tasks WHERE deleted_at IS NULL
ORDER BY (pinned_at IS NOT NULL) DESC, pinned_at DESC NULLS LAST, id DESC'''))]
        self._hydrate(tasks)
        return tasks

    def get(self, task_id: int) -> Task | None:
        task = _row(self.executor, Query(f'SELECT {TASK_COLS} FROM tasks WHERE id=$1 AND deleted_at IS NULL', (int64(task_id, 'task id'),)))
        if task is not None:
            self._hydrate([task])
        return task

    def create(self, description: str, goal: str, options: TaskCreateOptions | None = None) -> Task:
        opts = options or TaskCreateOptions()
        if not all(isinstance(v, str) for v in (description, goal, opts.name)):
            raise TaskInvalid('description, goal and name must be strings')
        if opts.intercept_rules:
            raise NotImplementedError('task intercept-rule creation is not ported; refusing to omit rules')
        if len(opts.source_task_ids) > MAX_TASK_SOURCE_COUNT:
            raise TaskInvalid('at most 8 source tasks are allowed')
        sources = _ids(opts.source_task_ids, 'source task id')
        companies = _ids(opts.company_ids, 'company id', deduplicate=True, maximum=MAX_TASK_COMPANY_COUNT)
        profiles = _ids(opts.llm_profile_ids, 'LLM profile id')
        timeout = max(0, int64(opts.timeout_seconds, 'timeout seconds'))
        heartbeat = max(MIN_PLAN_HEARTBEAT_SECONDS, int64(opts.plan_heartbeat_seconds, 'heartbeat seconds'))
        if timeout > 2**31-1 or heartbeat > 2**31-1:
            raise TaskInvalid('timeout and heartbeat must fit PostgreSQL INTEGER')
        if opts.coverage_enabled is not None and type(opts.coverage_enabled) is not bool:
            raise TaskInvalid('coverage_enabled must be boolean or None')
        coverage = opts.coverage_enabled is not False
        if opts.category_id is not None and int64(opts.category_id, 'category id') <= 0:
            raise CategoryInvalid('category id must be positive')
        transaction = getattr(self.executor, 'transaction', None)
        if transaction is None:
            raise TypeError('task creation requires a transaction-capable executor')
        with transaction() as tx:
            exp_id = tx.fetch_one(Query('INSERT INTO explorations(description, goal) VALUES ($1,$2) RETURNING id', (description, goal)))[0]
            payload = json.dumps({'summary': f'Task origin: {description}; goal: {goal}', 'description': description, 'goal': goal}, ensure_ascii=False)
            tx.execute(Query("INSERT INTO exploration_nodes(exploration_id, kind, payload, priority, state, origin) VALUES ($1, 'fact', $2, 0, 'origin', 'system')", (exp_id, payload)))
            if opts.category_id is not None:
                if not tx.fetch_all(Query('SELECT name FROM task_categories WHERE id=$1 FOR KEY SHARE', (opts.category_id,))):
                    raise CategoryNotFound('task category not found')
            active = profiles[0] if profiles else None
            row = tx.fetch_one(Query(f'''INSERT INTO tasks(name, category_id, description, goal, exploration_id, llm_profile_id, active_llm_profile_id, timeout_seconds, plan_heartbeat_seconds, coverage_enabled)
VALUES ($1,$2,$3,$4,$5,$6,$6,$7,$8,$9) RETURNING {TASK_COLS}''',
                (opts.name, opts.category_id, description, goal, exp_id, active, timeout, heartbeat, coverage)))
            task = Task(*row)
            for source in sources:
                if source == task.id:
                    raise TaskInvalid('a task cannot inherit from itself')
                count = tx.execute(Query('''INSERT INTO task_relations(task_id, source_task_id)
SELECT $1, id FROM tasks WHERE id=$2 AND deleted_at IS NULL''', (task.id, source)))
                if count != 1:
                    raise TaskSourceNotFound(f'source task {source} not found')
            if companies:
                self._insert_companies(tx, task.id, companies)
            for position, profile in enumerate(profiles):
                tx.execute(Query('INSERT INTO task_llm_profiles(task_id, profile_id, position) VALUES ($1,$2,$3)', (task.id, profile, position)))
            task.source_task_ids, task.company_ids, task.llm_profile_ids = sources, companies, profiles
            task.llm_failover_state = 'ready' if profiles else 'default'
        return task

    @staticmethod
    def _insert_companies(tx, task_id, company_ids):
        tx.fetch_one(Query('SELECT pg_advisory_xact_lock($1)', (7337741003,)))
        count = tx.fetch_one(Query('''WITH requested(company_id, position) AS (
    SELECT company_id, position FROM unnest($2::bigint[]) WITH ORDINALITY AS requested(company_id, position)
), inserted AS (
    INSERT INTO task_scope(task_id, kind, company_id, source, reason)
    SELECT $1, 'company', companies.id, 'manual', 'Company linked at task creation'
    FROM requested JOIN companies ON companies.id=requested.company_id
    ORDER BY requested.position RETURNING company_id
) SELECT count(*) FROM inserted''', (task_id, company_ids)))[0]
        if count != len(company_ids):
            raise TaskCompanyNotFound('one or more companies do not exist')
        tx.execute(Query('''UPDATE assets
SET task_ids=CASE WHEN $1=ANY(task_ids) THEN task_ids ELSE array_append(task_ids, $1) END
WHERE company_id=ANY($2::bigint[])''', (task_id, company_ids)))
        tx.execute(Query('''INSERT INTO task_asset_links(task_id, asset_id, source, source_summary)
SELECT $1, asset.id, $3, 'Company linked at task creation: ' || company.name
FROM assets asset JOIN companies company ON company.id=asset.company_id
WHERE asset.company_id=ANY($2::bigint[]) AND $1=ANY(asset.task_ids)
ON CONFLICT (task_id, asset_id) DO UPDATE SET source=EXCLUDED.source,
source_summary=EXCLUDED.source_summary, source_node_id=NULL''', (task_id, company_ids, 'company')))

    def update(self, task_id: int, *, name: str | None = None, pinned: bool | None = None) -> Task | None:
        if name is not None and not isinstance(name, str):
            raise TaskInvalid('name must be a string')
        if pinned is not None and type(pinned) is not bool:
            raise TaskInvalid('pinned must be boolean')
        return _row(self.executor, Query(f'''UPDATE tasks SET
name = CASE WHEN $2::boolean THEN $3 ELSE name END,
pinned_at = CASE WHEN $4::boolean IS NULL THEN pinned_at
 WHEN $4::boolean THEN COALESCE(pinned_at, now()) ELSE NULL END
WHERE id=$1 AND deleted_at IS NULL RETURNING {TASK_COLS}''', (int64(task_id, 'task id'), name is not None, name, pinned)))

    def apply_pause(self, task_id: int) -> Task | None:
        return _row(self.executor, Query(f'''UPDATE tasks SET paused=true, queued=false, queued_at=NULL
WHERE id=$1 AND deleted_at IS NULL AND paused=false AND status NOT IN ('done','failed','timeout')
RETURNING {TASK_COLS}''', (int64(task_id, 'task id'),)))

    def apply_admission(self, task_id, expected_status, status='running', queued=False,
                        mode='', preserve_position=False) -> Task | None:
        if expected_status not in STATUSES or status not in STATUSES:
            raise TaskInvalid('invalid admission status')
        if type(queued) is not bool or type(preserve_position) is not bool:
            raise TaskInvalid('admission flags must be boolean')
        if queued and mode not in ('bootstrap', 'resume'):
            raise TaskInvalid('queue mode must be bootstrap or resume')
        if not queued:
            mode = ''
        return _row(self.executor, Query(f'''UPDATE tasks SET status=$2,
completed_at=CASE WHEN $2 IN ('done','failed','timeout') THEN COALESCE(completed_at, now()) ELSE NULL END,
paused=false, queued=$3,
queued_at=CASE WHEN NOT $3 THEN NULL WHEN $5 AND queued THEN COALESCE(queued_at, now()) ELSE now() END,
queue_mode=CASE WHEN NOT $3 THEN ''
 WHEN ($5 AND queued AND queue_mode='bootstrap') OR $4='bootstrap' THEN 'bootstrap' ELSE 'resume' END,
first_run_at=CASE WHEN $6='timeout' AND $2 NOT IN ('done','failed','timeout') THEN NULL ELSE first_run_at END,
deadline_at=CASE WHEN $6='timeout' AND $2 NOT IN ('done','failed','timeout') THEN NULL ELSE deadline_at END
WHERE id=$1 AND deleted_at IS NULL AND status=$6 RETURNING {TASK_COLS}''',
            (int64(task_id, 'task id'), status, queued, mode, preserve_position, expected_status)))

    def set_parent_ref(self, task_id, parent_ref):
        if not isinstance(parent_ref, str):
            raise TaskInvalid('parent_ref must be a string')
        self.executor.execute(Query("UPDATE tasks SET parent_ref=NULLIF($2,'') WHERE id=$1", (int64(task_id, 'task id'), parent_ref)))

    def set_paused(self, task_id, paused):
        if type(paused) is not bool:
            raise TaskInvalid('paused must be boolean')
        self.executor.execute(Query('UPDATE tasks SET paused=$1 WHERE id=$2', (paused, int64(task_id, 'task id'))))

    def enqueue(self, task_id, mode):
        if mode not in ('bootstrap', 'resume'):
            raise TaskInvalid('queue mode must be bootstrap or resume')
        self.executor.execute(Query('''UPDATE tasks SET queued=true,
queued_at=CASE WHEN queued THEN COALESCE(queued_at, now()) ELSE now() END,
queue_mode=CASE WHEN queue_mode='bootstrap' OR $2='bootstrap' THEN 'bootstrap' ELSE 'resume' END
WHERE id=$1''', (int64(task_id, 'task id'), mode)))

    def dequeue(self, task_id, clear_mode=True):
        if type(clear_mode) is not bool:
            raise TaskInvalid('clear_mode must be boolean')
        self.executor.execute(Query("UPDATE tasks SET queued=false, queued_at=NULL, queue_mode=CASE WHEN $2 THEN '' ELSE queue_mode END WHERE id=$1", (int64(task_id, 'task id'), clear_mode)))

    def set_status(self, task_id, status):
        if status not in STATUSES:
            raise TaskInvalid('invalid task status')
        self.executor.execute(Query("""UPDATE tasks SET status=$1,
completed_at=CASE WHEN $1 IN ('done','failed','timeout') THEN COALESCE(completed_at, now()) ELSE NULL END
WHERE id=$2""", (status, int64(task_id, 'task id'))))

    def set_terminal_status_guarded(self, task_id, status):
        if not is_terminal(status):
            raise TaskInvalid('expected terminal task status')
        return self.executor.execute(Query("""UPDATE tasks SET status=$1, completed_at=COALESCE(completed_at, now())
WHERE id=$2 AND status NOT IN ('done','failed','timeout')""", (status, int64(task_id, 'task id')))) > 0

    def stamp_first_run(self, task_id, timeout_seconds):
        timeout_seconds = int64(timeout_seconds, 'timeout seconds')
        return self.executor.fetch_one(Query('''UPDATE tasks
SET first_run_at=COALESCE(first_run_at, now()),
deadline_at=CASE WHEN first_run_at IS NOT NULL THEN deadline_at
 WHEN $2 > 0 THEN now() + make_interval(secs => $2) ELSE NULL END
WHERE id=$1 RETURNING deadline_at''', (int64(task_id, 'task id'), timeout_seconds)))[0]

    def delete(self, task_id):
        raise NotImplementedError('task deletion requires unported cross-store locks and cleanup barriers')
