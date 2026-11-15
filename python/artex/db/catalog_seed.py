"""Explicit builtin metadata/variable seed, not application initialization.

Maps db/db.go builtinAgents and db/config.go SeedPromptIfEmpty/SavePrompt. Fixed
keys, roles and insertion defaults are preserved. English display prose only.
Unlike Go's repeated display/variable-description overwrite, existing rows are
left untouched to preserve user customizations. Deleted catalog rows may be
recreated only when this explicitly requested seed is run. No periodic seeding.

NOT ported here: code-default BuiltinPromptSeeds template bodies, prompt upgrade
flags, renamed-variable cleanup, shell-default upgrade, MCP/tool registration,
skill visibility, intercept rules or profiles/permissions. No security gate is
bypassed. enabled/interactive_shell are stored metadata, not runtime capability.
The prompt primitive requires an explicit caller-supplied reviewed template; it
never invents a fallback, treats an empty template as a default, or overwrites a
current prompt. SQL alone cannot coordinate live runtime cache invalidation;
run initialization offline or under the host configuration lifecycle barrier.
"""
from dataclasses import dataclass
from .core import Query,int64
from .postgres import NoRowsError
from .schema import LOCK_KEY

class CatalogSeedInvalid(ValueError):pass
class CatalogSeedMissing(LookupError):pass
class CatalogSeedUnavailable(RuntimeError):pass

# key, display name, persisted role, description, insertion shell flag, seconds.
BUILTINS=(
 ('goals','Goal decomposition','goals','Split the assessment goal into independent, verifiable subgoals.',False,1200),
 ('planner','Planner','planner','Read the current situation and assess goals; add exploration intents only for genuinely uncovered directions, with one planning loop per task.',False,1200),
 ('mainagent','Main','main','Human interface: observe progress and turn human intent into hints or high-priority intents.',False,1200),
 ('worker','Worker','worker','Execute one assigned intent, write discovered facts and vulnerabilities to the knowledge graph, then stop.',False,1200),
 ('auto','Auto','assistant','Platform operations assistant: manage tasks and assets using tools, and create or edit skills, custom tools and MCP configurations.',False,1200),
 ('pentest','Penetration testing','assistant','Standalone assessment agent: plan, execute and independently verify the full reconnaissance, attack-surface investigation, exploitation, verification and wrap-up workflow.',True,0),
)
VARIABLES={
 'goals':(('EngagementDescription','Task description: target and background','Test the example.com site','exploration'),),
 'planner':(('Goal','Overall task goal','Obtain administrator access to example.com','exploration'),('AssetSummary','Optional asset counts and type distribution','domain:3 ip:5 site:2','distilled')),
 'mainagent':(('Goal','Current task goal','Obtain administrator access to example.com','exploration'),('AssetSummary','Optional initial situation summary','domain:3 ip:5','distilled'),('FindingsSummary','Optional confirmed vulnerability summary','high:1 medium:2','distilled')),
 'worker':(('ProxyAddr','Recording proxy address, controlling conditional wording','127.0.0.1:8080','runtime'),('WorkerName','Optional worker self-identification','worker-1','runtime')),
}

@dataclass(frozen=True)
class CatalogSeedResult:
    agent_ids:dict
    runtime_ready:bool=False
    default_prompt_bodies_seeded:bool=False


def _action(action):
    if action!='seed_missing':raise CatalogSeedInvalid('Explicit seed_missing action required')

def _transaction(executor):
    if not callable(getattr(executor,'transaction',None)):raise CatalogSeedUnavailable('Transactional executor required')
    return executor.transaction()

def seed_builtin_catalog(executor,*,action=None):
    _action(action);ids={}
    with _transaction(executor) as tx:
        tx.fetch_one(Query('SELECT pg_advisory_xact_lock($1)',(LOCK_KEY,)))
        for key,name,role,description,shell,seconds in BUILTINS:
            tx.execute(Query('''INSERT INTO agents(key,name,description,role,builtin,enabled,interactive_shell,run_seconds)
VALUES ($1,$2,NULLIF($3,''),$4,true,true,$5,$6) ON CONFLICT (key) DO NOTHING''',(key,name,description,role,shell,seconds)))
            try:row=tx.fetch_one(Query('SELECT id,builtin,role FROM agents WHERE key=$1 FOR UPDATE',(key,)))
            except NoRowsError:raise CatalogSeedUnavailable('Builtin catalog row unavailable') from None
            if row is None or row[1] is not True or row[2]!=role:
                raise CatalogSeedUnavailable('Builtin key conflicts with existing catalog identity')
            ids[key]=row[0]
            for var,desc,example,source in VARIABLES.get(key,()):
                tx.execute(Query('''INSERT INTO agent_prompt_vars(agent_id,var_name,description,example,source)
VALUES ($1,$2,$3,$4,$5) ON CONFLICT (agent_id,var_name) DO NOTHING''',(row[0],var,desc,example,source)))
    return CatalogSeedResult(ids)


def seed_prompt_if_empty(executor,agent_id,template,*,action=None):
    """Return newly inserted version or None when a current prompt already exists."""
    _action(action);int64(agent_id,'agent ID')
    if agent_id<=0 or not isinstance(template,str) or not template.strip() or '\x00' in template or len(template)>1048576:
        raise CatalogSeedInvalid('Positive agent and explicit nonempty bounded template required')
    template.encode('utf-8')
    with _transaction(executor) as tx:
        tx.fetch_one(Query('SELECT pg_advisory_xact_lock($1)',(LOCK_KEY,)))
        try:row=tx.fetch_one(Query('SELECT current_prompt_id FROM agents WHERE id=$1 FOR UPDATE',(agent_id,)))
        except NoRowsError:raise CatalogSeedMissing('Agent not found') from None
        if row is None:raise CatalogSeedMissing('Agent not found')
        if row[0] is not None:return None
        version=tx.fetch_one(Query('SELECT COALESCE(max(version),0)+1 FROM agent_prompts WHERE agent_id=$1',(agent_id,)))[0]
        pid=tx.fetch_one(Query('''INSERT INTO agent_prompts(agent_id,version,template_text,note,updated_by)
VALUES ($1,$2,$3,$4,$5) RETURNING id''',(agent_id,version,template,'Builtin default','system')))[0]
        if tx.execute(Query('UPDATE agents SET current_prompt_id=$1 WHERE id=$2 AND current_prompt_id IS NULL',(pid,agent_id)))!=1:
            raise CatalogSeedUnavailable('Prompt pointer changed')
        return version
