"""Source main-session metadata only; never creates/resets a conversation.

Source db/exploration.go ListMainSessions appends implicit seq=0, whose Go zero
Time marshals as 0001-01-01T00:00:00Z. This is not the task creation timestamp.
Fixed live task/exploration ownership is required; archives fail closed. Parent
row SHARE lock supplements (does not replace) host revision/current authority.
"""
from datetime import datetime
from .core import Query
from .task_coverage import CoverageScope
class SessionDenied(PermissionError):pass
class SessionUnavailable(RuntimeError):pass
class MainSessionsStore:
    def __init__(self,executor,scope,*,authorize,current,lifecycle_guard,max_rows=1000):
        if type(scope) is not CoverageScope or type(scope.task_id) is not int or type(scope.exploration_id) is not int or not 0<scope.task_id<2**63 or not 0<scope.exploration_id<2**63 or not isinstance(scope.revision,str) or not 1<=len(scope.revision)<=256:raise ValueError('Exact task/exploration revision required')
        if any(not callable(v) for v in (authorize,current,lifecycle_guard)):raise ValueError('Explicit read grants required')
        if type(max_rows) is not int or not 1<=max_rows<=10000:raise ValueError('Invalid bound')
        self.executor,self.scope,self.guard=executor,scope,lifecycle_guard
        self._executor,self._scope,self._guard=executor,scope,lifecycle_guard
        self.authorize,self.current=authorize,current;self.max_rows=max_rows
    def read(self,*,project=None,disclosure=None):
        """Optional pure projection executes under guard; grants checked afterward.

        HTTP uses this to serialize before the final disclosure fence. Projection
        is not execution permission and may not mutate or call external services.
        """
        if project is not None and not callable(project):raise ValueError('Invalid projection')
        if disclosure is not None and not callable(disclosure):raise ValueError('Invalid disclosure policy')
        policies=(self.authorize,self.current,self.guard)
        def check():
            def pins():
                if self.executor is not self._executor or self.scope is not self._scope or self.guard is not self._guard or any(a is not b for a,b in zip(policies,(self.authorize,self.current,self.guard))):raise SessionDenied()
            pins()
            if policies[1](self._scope) is not True:raise SessionDenied()
            pins()
        def allow():
            check()
            if disclosure is not None and disclosure() is not True:raise SessionDenied()
            check()
            if policies[0](self._scope) is not True:raise SessionDenied()
            check()
        with policies[2](self._scope):
            allow()
            with self._executor.transaction() as tx:
                rows=tx.fetch_all(Query('SELECT id FROM tasks WHERE id=$1 AND exploration_id=$2 AND deleted_at IS NULL AND archived_at IS NULL AND NOT EXISTS(SELECT 1 FROM task_archives WHERE task_id=$1) FOR SHARE',(self._scope.task_id,self._scope.exploration_id)))
                if rows!=[(self._scope.task_id,)]:raise SessionUnavailable()
                allow()
                rows=tx.fetch_all(Query('SELECT seq,created_at FROM main_sessions WHERE exploration_id=$1 ORDER BY seq DESC LIMIT $2',(self._scope.exploration_id,self.max_rows+1)))
                allow()
                if len(rows)>self.max_rows:raise SessionUnavailable()
                sessions=[];previous=2**31
                for seq,created in rows:
                    if type(seq) is not int or not 0<seq<previous or not isinstance(created,datetime) or created.tzinfo is None:raise SessionUnavailable()
                    previous=seq;sessions.append(dict(seq=seq,created_at=created.isoformat().replace('+00:00','Z')))
                sessions.append(dict(seq=0,created_at='0001-01-01T00:00:00Z'))
                result=dict(sessions=sessions,current=sessions[0]['seq'])
                allow()
            allow()
            output=project(result) if project is not None else result
            allow()
        # Context-manager exit itself can invalidate a grant.
        with policies[2](self._scope):allow()
        return output
