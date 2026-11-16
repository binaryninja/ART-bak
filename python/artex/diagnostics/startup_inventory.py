"""Bounded single-statement startup observation, not ownership or readiness.

No locks, writes, schema installation, providers, transcript access or launch.
Visible prepared bootstrap and archive rows are included conservatively.
Empty means no visible matching rows at the statement snapshot. RLS or views can
hide work; this does not certify schema authenticity or an unfiltered inventory.
"""
from dataclasses import dataclass,field
from ..db.core import Query

@dataclass(frozen=True)
class InventoryScope:
    database:str
    schema:str='public'

@dataclass(frozen=True)
class InventoryReference:
    category:str
    task_id:int|None
    entity_id:int
    state:str

@dataclass(frozen=True)
class StartupInventory:
    state:str
    total:int|None
    references:tuple[InventoryReference,...]=()
    visibility:str=field(default='database_role_visible_rows',init=False)
    ownership_proven:bool=field(default=False,init=False)
    launch_authorized:bool=field(default=False,init=False)
    recovery_supported:bool=field(default=False,init=False)

_SQL="""WITH observed AS (
 SELECT 'task_running'::text AS category,id AS task_id,id AS entity_id,status AS state FROM public.tasks WHERE status='running'
 UNION ALL SELECT 'task_paused',id,id,status FROM public.tasks WHERE paused OR status='paused'
 UNION ALL SELECT 'task_queued',id,id,status FROM public.tasks WHERE queued
 UNION ALL SELECT 'invalid_task',id,id,status FROM public.tasks WHERE status IS NULL OR status NOT IN ('created','running','paused','done','failed','timeout') OR paused IS NULL OR queued IS NULL
 UNION ALL SELECT 'bootstrap',task_id,run_id,phase FROM public.artex_task_bootstrap
 UNION ALL SELECT 'worker_claim',t.id,n.id,n.state FROM public.exploration_nodes n
 LEFT JOIN public.tasks t ON t.exploration_id=n.exploration_id
 WHERE n.kind='intent' AND (n.state IS NULL OR n.state NOT IN ('open','running','paused','done','blocked','exhausted','stopped','deleted') OR n.state IN ('running','paused') OR COALESCE(n.owner,'')<>'')
 UNION ALL SELECT 'archive',task_id,id,state FROM public.task_archives
), counted AS (SELECT count(*) AS total FROM observed), bounded AS (
 SELECT category,task_id,entity_id,state FROM observed ORDER BY category,task_id NULLS FIRST,entity_id LIMIT $1
)
SELECT current_database(),c.total,b.category,b.task_id,b.entity_id,
 CASE WHEN octet_length(b.state)<=64 THEN b.state ELSE NULL END
FROM counted c LEFT JOIN bounded b ON c.total<=$2 ORDER BY b.category,b.task_id NULLS FIRST,b.entity_id"""
_STATES={
 'task_running':{'running'},
 'task_paused':{'created','running','paused','done','failed','timeout'},
 'task_queued':{'created','running','paused','done','failed','timeout'},
 'bootstrap':{'running','prepared','unresolved'},
 'worker_claim':{'open','running','paused','done','blocked','exhausted','stopped','deleted'},
 'archive':{'archive_queued','archiving','archive_failed','ready','restore_queued','restoring','restore_failed','delete_queued','deleting','delete_failed'},
}

class StartupInventoryReader:
    def __init__(self,executor,scope,*,authorize,current,lifecycle_guard,max_references=500):
        if type(scope) is not InventoryScope or type(scope.database) is not str or not 1<=len(scope.database.encode())<=256 or '\0' in scope.database or scope.schema!='public':raise ValueError('Explicit public database scope required')
        if any(not callable(f) for f in (authorize,current,lifecycle_guard)):raise ValueError('Explicit diagnostic authority required')
        if type(max_references) is not int or not 1<=max_references<=1000:raise ValueError('Bounded diagnostic references required')
        self._pins=(executor,scope,authorize,current,lifecycle_guard,max_references);self._original=self._pins
        self._identity=(scope.database,scope.schema)
    def read(self):
        executor,scope,authorize,current,guard,maximum=self._original
        def pure():
            if self._pins is not self._original or (scope.database,scope.schema)!=self._identity:raise PermissionError('Startup inventory access denied')
        def authority():
            pure()
            try:allowed=authorize(scope)
            except Exception:raise PermissionError('Startup inventory access denied') from None
            if allowed is not True:raise PermissionError('Startup inventory access denied')
            pure()
        def live():
            pure()
            try:valid=current(scope)
            except Exception:raise PermissionError('Startup inventory access denied') from None
            if valid is not True:raise PermissionError('Startup inventory access denied')
            pure()
        authority();result=StartupInventory('unavailable',None)
        try:
            with guard(scope):
                live();authority()
                rows=executor.fetch_all(Query(_SQL,(maximum+1,maximum)))
                live();authority()
                result=self._decode(rows,scope.database,maximum)
        except PermissionError:raise PermissionError('Startup inventory access denied') from None
        except Exception:result=StartupInventory('unavailable',None)
        # Even unavailable/schema/overflow diagnostics require fresh authority.
        live();authority();pure()
        return result
    @staticmethod
    def _decode(rows,database,maximum):
        if type(rows) not in (list,tuple) or not rows or len(rows)>maximum:raise ValueError()
        if any(type(row) not in (list,tuple) or len(row)!=6 for row in rows):raise ValueError()
        total=rows[0][1]
        if type(total) is not int or not 0<=total<=maximum:raise ValueError()
        if any(row[0]!=database or row[1]!=total or type(row[1]) is not int for row in rows):raise ValueError()
        if total==0:
            if len(rows)!=1 or any(value is not None for value in rows[0][2:]):raise ValueError()
            return StartupInventory('observed_no_visible_known_work',0)
        if len(rows)!=total:raise ValueError()
        refs=[];seen=set()
        for _,_,category,task,entity,state in rows:
            if type(category) is not str or category not in _STATES or type(state) is not str or state not in _STATES[category]:raise ValueError()
            if type(entity) is not int or not 0<entity<2**63:raise ValueError()
            if task is None:
                if category!='worker_claim':raise ValueError()
            elif type(task) is not int or not 0<task<2**63:raise ValueError()
            key=(category,task,entity)
            if key in seen:raise ValueError()
            seen.add(key);refs.append(InventoryReference(category,task,entity,state))
        return StartupInventory('observed_records',total,tuple(refs))
