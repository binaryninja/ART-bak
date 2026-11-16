"""Read original correlated activity only. Navigation never executes a tool."""
from dataclasses import dataclass
from datetime import timezone
import re
from .core import Query
from .intercept_detail_read import InterceptDetailReader,_time

class ExecutionUnavailable(RuntimeError):pass

@dataclass(frozen=True)
class ExecutionParent:
    kind:str
    id:int
    task_key:str|None=None
    exploration_id:int|None=None
    tool_use_id:str=''

@dataclass(frozen=True)
class ExecutionNavigation:
    status:int
    body:dict
    parents:tuple
    identity:tuple

class InterceptExecutionReader:
    def __init__(self,detail,*,max_bytes=1048576):
        if type(detail) is not InterceptDetailReader or type(max_bytes) is not int or not 1024<=max_bytes<=1048576:raise ValueError('Exact detail reader and finite activity bounds required')
        detail.validate();self.detail=detail;self.executor=detail.executor;self.scope=detail.scope;self.max_bytes=max_bytes
        self._pins=(detail,detail.executor,detail.scope,max_bytes)
    def validate(self):
        d,e,s,b=self._pins
        if self.detail is not d or self.executor is not e or d.executor is not e or self.scope is not s or d.scope is not s or type(self.max_bytes) is not int or self.max_bytes!=b:raise ExecutionUnavailable()
        d.validate()
    def read(self,id,hint,*,authorize_parent):
        self.validate()
        if type(id) is not int or not 0<id<2**63 or (hint is not None and (type(hint) is not int or not 0<hint<2**63)) or not callable(authorize_parent):raise ValueError('Exact navigation selection required')
        parents=[]
        def allow(parent):
            self.validate()
            if authorize_parent(parent) is not True:raise PermissionError()
            self.validate();parents.append(parent)
        def result(status,body,identity):return ExecutionNavigation(status,body,tuple(parents),identity)
        detail=self.detail.read(id);self.validate()
        if detail is None:
            if hint is not None:
                parent=ExecutionParent('conversation_hint',hint);allow(parent)
                rows=self.executor.fetch_all(Query('SELECT EXISTS(SELECT 1 FROM conversations WHERE id=$1)',(hint,)));self.validate()
                if type(rows) not in (list,tuple) or len(rows)!=1 or type(rows[0]) not in (list,tuple) or len(rows[0])!=1 or type(rows[0][0]) is not bool:raise ExecutionUnavailable()
                if rows[0][0] is False:return result(410,{'error':'Conversation has been deleted'},('missing',hint,False))
            return result(404,{'error':'Approval record has been deleted or does not exist'},('missing',hint))
        audit=detail['audit'];conversation=detail['conversation_id'];task_key=detail['task_id'];tool=detail['tool_name']
        correlation=None if audit is None else audit['correlation'];use='' if audit is None else audit.get('tool_use_id','')
        identity=(id,conversation,task_key,tool,correlation,use)
        def unavailable():return result(409,{'error':'Original tool call cannot be uniquely correlated'},identity)
        if correlation!='exact' or not use:return unavailable()
        if len(use.encode())>65536 or len(tool.encode())>65536:raise ExecutionUnavailable()
        if conversation is not None:
            parent=ExecutionParent('conversation',conversation,task_key,tool_use_id=use);allow(parent)
            columns="id,NULL::bigint AS node_id,COALESCE(worker,'') AS worker,kind,COALESCE(tool,'') AS tool,tool_use_id,is_error,COALESCE(summary,'') AS summary,created_at,NULL::integer AS main_seg"
            source='conversation_activities WHERE conversation_id=$1';owner=conversation
        elif task_key is not None:
            if not re.fullmatch('[+-]?[0-9]+',task_key):return unavailable()
            digits=task_key.lstrip('+-').lstrip('0') or '0'
            if len(digits)>19:return unavailable()
            owner=int(('-' if task_key.startswith('-') else '')+digits)
            if not 0<owner<2**63:return unavailable()
            allow(ExecutionParent('task',owner,task_key,tool_use_id=use))
            rows=self.executor.fetch_all(Query('SELECT exploration_id FROM tasks WHERE id=$1 AND archived_at IS NULL AND deleted_at IS NULL',(owner,)));self.validate()
            if not rows:return result(410,{'error':'Task has been deleted or archived'},identity)
            if len(rows)!=1 or len(rows[0])!=1 or type(rows[0][0]) is not int or rows[0][0]<=0:raise ExecutionUnavailable()
            parent=ExecutionParent('task',owner,task_key,rows[0][0],use);allow(parent)
            columns="id,node_id,COALESCE(worker,'') AS worker,kind,COALESCE(tool,'') AS tool,tool_use_id,is_error,COALESCE(summary,'') AS summary,created_at,main_seg"
            source='activity WHERE exploration_id=$1';owner=parent.exploration_id
            identity=(*identity,parent.exploration_id)
        else:return unavailable()
        sql='WITH selected AS (SELECT '+columns+' FROM '+source+" AND tool_use_id=$2 AND kind IN ('tool_use','tool_result') ORDER BY id LIMIT 3), bounded AS (SELECT *,SUM(octet_length(worker)+octet_length(kind)+octet_length(tool)+octet_length(tool_use_id)+octet_length(summary)) OVER () AS bytes FROM selected) SELECT bytes<=$3,id,node_id,"+','.join('CASE WHEN bytes<=$3 THEN '+f+' ELSE NULL END' for f in ('worker','kind','tool','tool_use_id'))+',is_error,CASE WHEN bytes<=$3 THEN summary ELSE NULL END,created_at,main_seg FROM bounded ORDER BY id'
        rows=self.executor.fetch_all(Query(sql,(owner,use,self.max_bytes)));self.validate()
        if type(rows) not in (tuple,list) or len(rows)>3:raise ExecutionUnavailable()
        if not rows:return result(410,{'error':'Original session or activity has been deleted or does not exist'},identity)
        if len(rows)>2:return unavailable()
        items=[];raw=[];used=0
        for row in rows:
            if type(row) not in (tuple,list) or len(row)!=11 or row[0] is not True:raise ExecutionUnavailable()
            _,seq,node,worker,kind,name,uid,error,summary,created,segment=row
            if type(seq) is not int or not 0<seq<2**63 or type(error) is not bool:raise ExecutionUnavailable()
            if node is not None and (type(node) is not int or not -(2**63)<=node<2**63):raise ExecutionUnavailable()
            if segment is not None and (type(segment) is not int or not -(2**31)<=segment<2**31):raise ExecutionUnavailable()
            for text in (worker,kind,name,uid,summary):
                if type(text) is not str:raise ExecutionUnavailable()
                used+=len(text.encode())
            if used>self.max_bytes or uid!=use or kind not in ('tool_use','tool_result'):raise ExecutionUnavailable()
            _time(created)
            item=dict(seq=seq,worker=worker,kind=kind,is_error=error,summary=summary,ts=created.isoformat(timespec='seconds').replace('+00:00','Z'))
            if node is not None:item['intent_id']=str(node)
            if name:item['tool']=name
            if uid:item['tool_use_id']=uid
            if segment is not None:item['main_seg']=segment
            items.append(item);raw.append((seq,node,worker,kind,name,segment))
        call=raw[0]
        if call[3]!='tool_use' or call[4]!=tool:return unavailable()
        if len(raw)==2:
            paired=raw[1]
            if paired[0]<=call[0] or paired[3]!='tool_result' or paired[2]!=call[2] or paired[4] not in ('',call[4]) or paired[1]!=call[1] or (paired[5] or 0)!=(call[5] or 0):return unavailable()
        session=''
        if conversation is None:
            if call[2]=='mainagent':session='main:'+str(call[5] or 0)
            elif call[2]=='planner':session='plan'
            elif call[1] is not None:session='intent:'+str(call[1])
            else:return unavailable()
        return result(200,dict(conversation_id=conversation,task_id=task_key,session=session,seq=call[0],items=items),identity)
