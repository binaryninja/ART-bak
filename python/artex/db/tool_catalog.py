"""Tool metadata storage only, grounded in db/tools.go and db/tool_usage.go.

No executor, tool registration, approval grant, script loading or network calls.
Source has no approval column: enabled/agents never constitute runtime consent.
JSON exec values are opaque persisted data. No credential discovery or expansion.
Writes lock the existing tool before referenced agents; unknown bindings fail
closed (stricter than source loose JSON arrays). All writes are transactional.
Builtin schema structure is fixed; only description/default annotations change.
Custom deletion removes only its catalog row, exactly the source persistence
scope; existing host-owned callbacks are not revoked or drained by this operation.
Hosts must not treat catalog deletion as cancellation of already executing work.
"""
from copy import deepcopy
from dataclasses import dataclass,asdict
import json,re
from .core import Query
from .postgres import NoRowsError

class ToolInvalid(ValueError):pass
class ToolMissing(LookupError):pass
class ToolConflict(RuntimeError):pass
class ToolUnavailable(RuntimeError):pass

COLUMNS='key, system, description, schema, agents, enabled, kind, exec, deferred'

def key(value,*,custom=False):
    pattern=r'[a-z][a-z0-9_]{0,127}' if custom else r'[A-Za-z][A-Za-z0-9_.:-]{0,127}'
    if not isinstance(value,str) or not re.fullmatch(pattern,value):raise ToolInvalid('Invalid tool key')
    return value

def _pairs(items):
    result={}
    for k,v in items:
        if k in result:raise ToolInvalid('Duplicate JSON field')
        result[k]=v
    return result

def value_json(value):
    try:
        if isinstance(value,(str,bytes)):value=json.loads(value,object_pairs_hook=_pairs,parse_constant=lambda _:(_ for _ in ()).throw(ToolInvalid()))
        encoded=json.dumps(value,ensure_ascii=False,allow_nan=False,separators=(',',':'))
        if len(encoded.encode('utf-8'))>1048576:raise ToolInvalid('JSON exceeds bound')
        return json.loads(encoded)
    except (ValueError,UnicodeError,TypeError,RecursionError):raise ToolInvalid('Invalid bounded JSON') from None

@dataclass(frozen=True)
class CatalogTool:
    key:str
    system:bool
    description:str
    schema:dict
    agents:list
    enabled:bool
    kind:str
    exec:dict
    deferred:bool
    def to_dict(self,*,calls=0):return dict(asdict(self),calls=calls)

def _row(row):
    if row is None:return None
    if len(row)!=9:raise ToolUnavailable('Invalid catalog row')
    values=list(row)
    for i in (3,4,7):values[i]=value_json(values[i])
    if values[4] is None:values[4]=[]
    return CatalogTool(*values)

def validate(tool,*,custom=True):
    if not isinstance(tool,CatalogTool):raise ToolInvalid('Tool metadata required')
    key(tool.key,custom=custom)
    if not isinstance(tool.description,str) or '\0' in tool.description:raise ToolInvalid('Invalid description')
    try:description_bytes=tool.description.encode('utf-8')
    except UnicodeEncodeError:raise ToolInvalid('Invalid description') from None
    if len(description_bytes)>65536:raise ToolInvalid('Invalid description')
    if any(type(v) is not bool for v in (tool.system,tool.enabled,tool.deferred)):raise ToolInvalid('Invalid flags')
    if custom and (tool.system or tool.kind not in ('command','script','http','shell')):raise ToolInvalid('Invalid custom kind')
    schema=value_json(tool.schema);execution=value_json(tool.exec)
    if not isinstance(schema,dict) or not isinstance(execution,dict):raise ToolInvalid('Schema and exec must be objects')
    if custom and tool.kind=='http' and (not isinstance(schema.get('properties'),dict) or not schema['properties']):raise ToolInvalid('HTTP tool requires schema properties')
    if not isinstance(tool.agents,list) or len(tool.agents)>1000 or any(not isinstance(a,str) for a in tool.agents) or len(set(tool.agents))!=len(tool.agents):raise ToolInvalid('Invalid bindings')
    for agent in tool.agents:
        if not isinstance(agent,str) or not re.fullmatch(r'[a-z][a-z0-9_]{0,127}',agent):raise ToolInvalid('Invalid agent binding')
    return json.dumps(schema),json.dumps(tool.agents),json.dumps(execution)

def _structure(schema):
    # Ignore annotation fields only at schema nodes, not property names.
    if not isinstance(schema,dict):return schema
    result={k:deepcopy(v) for k,v in schema.items() if k not in ('description','default')}
    for k in ('properties','$defs','definitions','patternProperties'):
        if isinstance(result.get(k),dict):result[k]={n:_structure(v) for n,v in result[k].items()}
    for k in ('items','additionalProperties','contains','not','if','then','else'):
        if isinstance(result.get(k),dict):result[k]=_structure(result[k])
    for k in ('allOf','anyOf','oneOf','prefixItems'):
        if isinstance(result.get(k),list):result[k]=[_structure(v) for v in result[k]]
    return result

class ToolCatalogStore:
    def __init__(self,executor):self.executor=executor
    def list(self):return [_row(r) for r in self.executor.fetch_all(Query('SELECT '+COLUMNS+' FROM tools ORDER BY key',()))]
    def get(self,identifier):
        rows=self.executor.fetch_all(Query('SELECT '+COLUMNS+' FROM tools WHERE key=$1',(key(identifier),)))
        return _row(rows[0]) if rows else None
    def usage_counts(self):
        result={}
        for name,count in self.executor.fetch_all(Query('SELECT tool_key, COUNT(*) FROM tool_usage GROUP BY tool_key',())):
            if type(count) is not int or count<0:raise ToolUnavailable('Invalid usage count')
            result[name]=count
        return result
    def _transaction(self):
        if not callable(getattr(self.executor,'transaction',None)):raise ToolUnavailable('Transaction adapter required')
        return self.executor.transaction()
    @staticmethod
    def _bindings(tx,agents):
        for agent in sorted(agents):
            rows=tx.fetch_all(Query('SELECT id FROM agents WHERE key=$1 FOR KEY SHARE',(agent,)))
            if not rows:raise ToolInvalid('Unknown agent binding')
    def create(self,tool):
        schema,agents,execution=validate(tool)
        with self._transaction() as tx:
            self._bindings(tx,tool.agents)
            try:
                row=tx.fetch_one(Query('INSERT INTO tools(key,system,description,schema,agents,enabled,kind,exec,deferred) VALUES ($1,false,$2,$3::jsonb,$4::jsonb,$5,$6,$7::jsonb,$8) ON CONFLICT (key) DO NOTHING RETURNING key',
                    (tool.key,tool.description,schema,agents,tool.enabled,tool.kind,execution,tool.deferred)))
            except NoRowsError:raise ToolConflict('Tool key exists') from None
            if row is None:raise ToolConflict('Tool key exists')
    def update(self,tool,*,custom=True):
        schema,agents,execution=validate(tool,custom=custom)
        with self._transaction() as tx:
            rows=tx.fetch_all(Query('SELECT '+COLUMNS+' FROM tools WHERE key=$1 FOR UPDATE',(tool.key,)))
            if not rows:raise ToolMissing()
            current=_row(rows[0])
            if custom and current.system:raise ToolInvalid('Only custom tools can be edited here')
            if current.system and _structure(current.schema)!=_structure(tool.schema):raise ToolInvalid('Builtin schema structure cannot change')
            self._bindings(tx,tool.agents)
            if custom:
                sql='UPDATE tools SET description=$2,schema=$3::jsonb,agents=$4::jsonb,enabled=$5,kind=$6,exec=$7::jsonb,deferred=$8 WHERE key=$1 AND system=false'
                params=(tool.key,tool.description,schema,agents,tool.enabled,tool.kind,execution,tool.deferred)
            else:
                sql='UPDATE tools SET description=$2,schema=$3::jsonb,agents=$4::jsonb,enabled=$5 WHERE key=$1'
                params=(tool.key,tool.description,schema,agents,tool.enabled)
            if tx.execute(Query(sql,params))!=1:raise ToolMissing()
    def delete(self,identifier):
        identifier=key(identifier,custom=True)
        with self._transaction() as tx:
            rows=tx.fetch_all(Query('SELECT '+COLUMNS+' FROM tools WHERE key=$1 FOR UPDATE',(identifier,)))
            if not rows:return
            if _row(rows[0]).system:raise ToolInvalid('Builtin tool cannot be deleted')
            if tx.execute(Query('DELETE FROM tools WHERE key=$1 AND system=false',(identifier,)))!=1:raise ToolMissing()
