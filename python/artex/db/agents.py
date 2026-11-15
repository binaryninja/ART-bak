"""Persisted agent catalog, not executable registrations or automatic seeds.

Source db/config.go. enabled/role/tool counts are stored configuration, NEVER a
claim of Python runtime capability. create_metadata deliberately does not seed a
prompt, install tools or register an agent. Explicit create() additionally writes
a starter prompt in the same transaction, without runtime registration.
Delete/config/prompt-edit mutation lifecycles remain unported.
Reads use separate SQL snapshots, matching source (not transactional aggregate).
Unlike source best-effort counts, failures propagate instead of fabricated zeros.
"""
from dataclasses import dataclass
import re
from .core import Query
from .postgres import NoRowsError

class AgentInvalid(ValueError):pass
class AgentConflict(RuntimeError):pass
class AgentMissing(LookupError):pass
class AgentUnavailable(RuntimeError):pass

COLUMNS="id,key,name,COALESCE(description,''),role,builtin,enabled,COALESCE(max_turns,0),COALESCE(run_seconds,600),COALESCE(web_search,false),COALESCE(interactive_shell,false),COALESCE(wrapup_prompt,''),COALESCE(wrapup_max_turns,0),COALESCE(task_timeout_wrapup_prompt,''),COALESCE(task_timeout_wrapup_max_turns,0),COALESCE(trigger_run_mode,'serial'),COALESCE(trigger_merge_mode,'all'),COALESCE(trigger_max_parallel,5),llm_profile_id"


def key(value):
    if not isinstance(value,str) or not re.fullmatch(r'[a-z][a-z0-9_]{0,127}',value):
        raise AgentInvalid('Invalid agent key')
    return value


def metadata(name,description):
    if not isinstance(name,str) or not isinstance(description,str):raise AgentInvalid('Invalid metadata')
    name=name.strip()
    if not name or len(name)>200 or len(description)>65536 or '\0' in name+description:raise AgentInvalid('Invalid metadata')
    (name+description).encode('utf-8')
    return name,description


@dataclass(frozen=True)
class Agent:
    id:int
    key:str
    name:str
    description:str
    role:str
    builtin:bool
    enabled:bool
    max_turns:int
    run_seconds:int
    web_search:bool
    interactive_shell:bool
    wrapup_prompt:str
    wrapup_max_turns:int
    task_timeout_wrapup_prompt:str
    task_timeout_wrapup_max_turns:int
    trigger_run_mode:str
    trigger_merge_mode:str
    trigger_max_parallel:int
    llm_profile_id:int|None

    def to_dict(self,*,mcp_count=0,skill_count=0,tool_count=0):
        names=('key','name','description','role','builtin','enabled','max_turns','run_seconds',
               'web_search','interactive_shell','llm_profile_id','trigger_run_mode','trigger_merge_mode','trigger_max_parallel')
        return dict(id=str(self.id),**{n:getattr(self,n) for n in names},
                    mcp_count=mcp_count,skill_count=skill_count,tool_count=tool_count)


def _row(row):
    if row is None:return None
    if len(row)!=19:raise AgentUnavailable('Invalid catalog row')
    return Agent(*row)


class AgentStore:
    def __init__(self,executor):self.executor=executor
    def list(self):
        return [_row(r) for r in self.executor.fetch_all(Query('SELECT '+COLUMNS+' FROM agents ORDER BY id',()))]
    def get(self,identifier):
        rows=self.executor.fetch_all(Query('SELECT '+COLUMNS+' FROM agents WHERE key=$1',(key(identifier),)))
        return _row(rows[0]) if rows else None
    def binding_counts(self):
        queries=("SELECT agent_id, count(DISTINCT resource_id) FROM agent_visibility WHERE resource_kind='mcp' AND enabled GROUP BY agent_id",
                 'SELECT agent_id, count(*) FROM agent_skill_visibility WHERE enabled GROUP BY agent_id',
                 'SELECT elem, count(*) FROM tools, jsonb_array_elements_text(agents) AS elem GROUP BY elem')
        result=[]
        for sql in queries:
            mapping={}
            for identifier,count in self.executor.fetch_all(Query(sql,())):
                if type(count) is not int or count<0:raise AgentUnavailable('Invalid binding count')
                mapping[identifier]=count
            result.append(mapping)
        return tuple(result)
    def create_metadata(self,identifier,name,description=''):
        identifier=key(identifier);name,description=metadata(name,description)
        return _row(self.executor.fetch_one(Query('INSERT INTO agents(key, name, description, role, builtin, enabled) VALUES ($1, $2, NULLIF($3,\'\'), \'assistant\', false, true) RETURNING '+COLUMNS,(identifier,name,description))))
    def create(self,identifier,name,description=''):
        """Explicit custom-agent creation plus English starter prompt, atomically.

        Stronger than source best-effort prompt seeding: any prompt failure rolls
        back the new agent. No startup seeds or runtime registration occurs.
        """
        identifier=key(identifier);name,description=metadata(name,description)
        if not callable(getattr(self.executor,'transaction',None)):
            raise AgentUnavailable('Transactional agent creation required')
        with self.executor.transaction() as tx:
            try:
                row=tx.fetch_one(Query("INSERT INTO agents(key, name, description, role, builtin, enabled) VALUES ($1, $2, NULLIF($3,''), 'assistant', false, true) ON CONFLICT (key) DO NOTHING RETURNING "+COLUMNS,(identifier,name,description)))
            except NoRowsError:
                raise AgentConflict('Agent key already exists') from None
            if row is None:raise AgentConflict('Agent key already exists')
            agent=_row(row)
            prompt=('You are a helpful AI assistant. Answer the user concisely and accurately in English; '
                    'use available tools when needed to complete the task. Do only what the user asks and do not invent information.')
            result=tx.fetch_one(Query("INSERT INTO agent_prompts(agent_id,version,template_text,note,updated_by) VALUES ($1,1,$2,'Built-in default','system') RETURNING id",(agent.id,prompt)))
            if not result or type(result[0]) is not int or result[0]<=0:raise AgentUnavailable('Prompt creation unconfirmed')
            if tx.execute(Query('UPDATE agents SET current_prompt_id=$1 WHERE id=$2',(result[0],agent.id)))!=1:
                raise AgentUnavailable('Prompt activation unconfirmed')
            return agent

    def update_metadata(self,identifier,name,description=''):
        identifier=key(identifier);name,description=metadata(name,description)
        changed=self.executor.execute(Query("UPDATE agents SET name=$2, description=NULLIF($3,'') WHERE key=$1 AND builtin=false",(identifier,name,description)))
        if changed!=1:raise AgentMissing('Custom agent no longer exists')
    def detail(self,agent):
        if not isinstance(agent,Agent):raise AgentInvalid('Agent row required')
        def rows(sql,params=None):return self.executor.fetch_all(Query(sql,(agent.id,) if params is None else params))
        prompt=rows('SELECT p.template_text FROM agents a JOIN agent_prompts p ON p.id=a.current_prompt_id WHERE a.id=$1')
        variables=[dict(zip(('name','description','example','source'),r)) for r in rows("SELECT var_name,COALESCE(description,''),COALESCE(example,''),source FROM agent_prompt_vars WHERE agent_id=$1 ORDER BY var_name")]
        versions=[]
        for version,template,note,stamp in rows("SELECT version,template_text,COALESCE(note,''),created_at FROM agent_prompts WHERE agent_id=$1 ORDER BY version DESC"):
            value=dict(version=version,template_text=template,ts=stamp.isoformat() if hasattr(stamp,'isoformat') else stamp)
            if note:value['note']=note
            versions.append(value)
        mcp=[r[0] for r in rows('SELECT resource_id FROM agent_visibility WHERE agent_id=$1 AND resource_kind=$2 AND enabled',(agent.id,'mcp'))]
        skills=[r[0] for r in rows('SELECT skill_name FROM agent_skill_visibility WHERE agent_id=$1 AND enabled ORDER BY skill_name')]
        profiles=[dict(zip(('id','name','model','is_default'),r)) for r in rows('SELECT id,name,model,is_default FROM llm_profiles ORDER BY id',())]
        return dict(prompt=prompt[0][0] or '' if prompt else '',variables=variables,versions=versions,
                    visibility={'mcp':mcp or None,'skill':skills},llm_profiles=profiles)
