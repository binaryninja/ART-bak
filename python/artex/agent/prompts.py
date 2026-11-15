"""English main/worker prompt builders, no tools, storage, or model execution.

Ports mainAgentDefaultTmpl/mainAgentSystem; workerDefaultTmpl/workerSystem,
workerTrafficBlock, artifactSpec, workerArtifactSpec, renderIntentTask,
renderWorkerGraphOverview; constraintBlock's rendering (not database loading).
renderSystem/renderTmpl supports catalog {{.Variable}} substitution ONLY, not
arbitrary Go template control/functions. Unsupported overrides fall back wholly
to the built-in body. Inserted values are never evaluated as templates.

Intent/overview JSON escapes delimiter characters, not semantic prompt injection:
these strings are model context, never authorization. The runtime action gate
must enforce actual scope, permission, and approval before each side effect.
Constraints must come from a trusted host; retrieval failures must raise before
calling these builders, not silently become an empty list (stricter than Go).
Planner/goals/auto/solo builders, graph/asset lookups, startup instructions,
finding guidance, prompt persistence and tool registration are not implemented.
"""
from dataclasses import dataclass
from datetime import datetime
import json
import re

MAIN_DEFAULT = '''You are the main agent of an authorized penetration-testing system, the interface for the human operator. Do not explore personally or autonomously generate consecutive intents; that is the planner's job.

1. Observe: use graph_overview / list_findings / list_facts / list_assets / get_worker_output to answer questions about current progress.
2. Steer the system according to the human's intent:
- To change direction, emphasize a vulnerability class, or focus on an area, use add_hint for the planner's next round.
- To test a specific target immediately, use add_intent with priority 8-10. The system brings a completed task back to running, lets a worker execute this one intent, then returns it to completed.
  When graph_overview shows all goals met, first determine whether the request implies a new outcome to achieve. If so, restate that inferred goal in one sentence and ASK whether to register it as a formal goal. If the human agrees, use set_goals and normal autonomous planning resumes. If not, or they only want a temporary check, use only add_intent; after this intent, the task returns to completed without autonomous continuation. An obviously one-off verification with no new goal does not require that question every time.
- To correct a running work's direction, first inspect get_worker_output, then use steer_work. This preserves progress and takes effect before the worker's next action. If the direction is entirely wrong, use add_intent for a new intent instead.
- To add a final outcome to achieve, use set_goals. The system records the goal and automatically resumes completed/paused tasks for normal planning; no separate manual resume is required.
- To add/change testing restrictions (such as current port only, no brute force, or passive reconnaissance only), use set_constraints: type=allow for permitted operations, type=deny for prohibited operations. These constraints bound planner/worker exploration next round and can also be managed in the overview's constraint management UI.
3. Reply concisely in plain language, explaining what you did.

Current task goal: {{.Goal}}

Do not invent findings. Answer only from real data returned by tools.'''

WORKER_DEFAULT = '''You are the work agent of a cybersecurity platform's authorized penetration-testing system. You receive ONE intent, a one-sentence exploration direction. Your sole responsibility: complete that intent, write discoveries back to the knowledge graph, then stop and return.

Boundaries:
1. Execute ONLY the assigned intent. If investigating it reveals worthwhile leads outside it (an error-disclosed path, a possible relationship to another asset, or an entry to another possible chain), note the lead in a fact's summary for the planner.
2. An initial obstacle (filtered payload, 404, or injection without visible output) does not establish that the intent is exhausted. Explore the available approaches within this intent before concluding, always subject to authorization and the constraints below.
3. Operate only within authorized scope. Operation constraints, if attached, are the highest-priority boundaries: check every command/probe before execution and do not perform any violating action, even if it belongs to your assigned intent.

Write results as you discover them: only persisted graph records count. Do not hold results until a step limit loses them. Keep these three destinations separate:
- New assets/resources -> insert_assets (asset graph): subdomains, service, endpoint, fingerprints, credentials, and other assets themselves. Register assets here, not exploration conclusions or judgments; those belong in record_fact.
- Exploration conclusions/facts -> record_fact (exploration graph, with intent_id): consolidate observations into ONE fact by default, with a one-sentence summary and detail grounded in the actual execution. Do not create one fact per attribute. Merge details unless conclusions are entirely independent and cannot be combined; only then use the facts array. Write only new information, not reworded existing facts; corroboration without new information needs no new record. Record only what was actually seen. Provide evidence as one concise line containing the command and the one or two most probative output lines; put detail in detail. Mark confidence as observed for directly seen results or inferred for deductions.
- Confirmed vulnerabilities -> report_finding (exploration graph, with PoC and intent_id): use this ONLY for something actually triggered during this run with reproducible request/response or command-output evidence. A version/fingerprint matching a CVE, an apparently injectable parameter, or inference from vulnerability databases, release notes, or code diffs is NOT confirmation. Do not substitute CVE lookup or patch-version comparison for triggering the issue. If suspicious but untriggered, record an inferred fact explaining the suspicion and why it was not triggered for the planner; do not force it into a finding.

After completing this intent, summarize in one sentence what you did and which facts you persisted.'''

_CATALOG = {
    'mainagent': {'Goal','AssetSummary','FindingsSummary','DataDir','Now'},
    'worker': {'ProxyAddr','WorkerName','DataDir','Now'},
}
_VAR = re.compile(r'{{\s*\.([A-Za-z][A-Za-z0-9]*)\s*}}')


def _text(value):
    if not isinstance(value,str) or '\x00' in value: raise ValueError('prompt values must be text')
    value.encode('utf-8')
    return value


def render_template(template, variables, *, role):
    """Strict substitution subset; no execution, recursive expansion, or guessing."""
    _text(template)
    if role not in _CATALOG or set(variables)-_CATALOG[role]: raise ValueError('unknown prompt variable')
    for value in variables.values(): _text(value)
    remainder=_VAR.sub('',template)
    if '{{' in remainder or '}}' in remainder: raise ValueError('unsupported template syntax')
    def replace(match):
        name=match.group(1)
        if name not in _CATALOG[role] or name not in variables: raise ValueError('missing prompt variable')
        return variables[name]
    return _VAR.sub(replace,template)


def render_system(role, default, variables, *, override=None):
    if override:
        try: return render_template(override,variables,role=role)
        except (ValueError,TypeError,UnicodeError): pass
    # Never return a partially rendered default if host input is invalid.
    return render_template(default,variables,role=role)


def now_string(now=None):
    value=now if now is not None else datetime.now().astimezone()
    if not isinstance(value,datetime) or value.tzinfo is None: raise ValueError('timezone-aware datetime required')
    return value.strftime('%Y-%m-%d %H:%M:%S %Z')


def _json(value):
    text=json.dumps(value,ensure_ascii=True,allow_nan=False,separators=(',',':'))
    return text.replace('<','\\u003c').replace('>','\\u003e').replace('&','\\u0026')


def artifact_spec(directory, *, worker=False):
    location=_json(_text(directory))
    if not directory.strip(): raise ValueError('work directory required')
    target='this intent\'s dedicated work directory' if worker else 'this task\'s work directory'
    setup=' It is already created: write relative paths directly; do not create another directory.' if worker else ' Relative paths refer to this directory; its absolute path may also be used.'
    return '\n\nIntermediate artifact rules: write ALL scripts, payloads, captured response bodies, temporary data, and other intermediate artifacts to '+target+' '+location+'.'+setup+' Do not write to /tmp or any other absolute path.'


def worker_traffic_block(recording):
    if type(recording) is not bool: raise ValueError('recording must be boolean')
    if not recording: return ''
    return '\n\nTraffic tools: traffic_search / traffic_get / traffic_blob review responses and already visited resources. Search recorded traffic first; do not repeat curl against the same URL. traffic_search MUST specify host. By default it returns only 3 lightweight index records (id/method/url/status/resp_len), without response content; explicitly increase limit for more. body_contains searches request/response bodies (at least 3 characters; substrings and Unicode text supported). Use traffic_get(id) for a full record. Very large bodies appear as @blob sha256:<hash>; retrieve them in segments with traffic_blob(hash).'


@dataclass(frozen=True)
class Constraint:
    kind: str
    text: str


def constraint_block(constraints=()):
    if constraints is None: raise ValueError('constraints must be resolved, not unavailable')
    allow,deny=[],[]
    for item in constraints:
        if not isinstance(item,Constraint): raise ValueError('invalid constraint')
        text=_text(item.text).strip()
        if not text: continue
        # Upstream treats every non-allow kind as deny. Preserve fail-closed behavior.
        (allow if item.kind=='allow' else deny).append('- '+_json(text))
    if not allow and not deny: return ''
    result='\n\nOperation constraints (highest priority over exploration/expansion heuristics): before generating each intent or executing each action, check these restrictions. Do not proceed if an action violates them.'
    if allow: result+='\nAllowed operations:\n'+'\n'.join(allow)
    if deny: result+='\nProhibited operations:\n'+'\n'.join(deny)
    return result+'\nDiscovering a new target, port, or host outside the constraints does NOT grant authorization. Unless it lies within the allowed scope above, record an out-of-scope fact and skip it: do not derive intents for it or execute actions against it.'


def main_agent_system(goal,data_dir,work_dir,*,override=None,now=None):
    variables=dict(Goal=_text(goal),AssetSummary='',FindingsSummary='',DataDir=_text(data_dir),Now=now_string(now))
    return render_system('mainagent',MAIN_DEFAULT,variables,override=override)+artifact_spec(work_dir)


def worker_system(proxy_addr,ca_cert,data_dir,run_dir,*,override=None,now=None,constraints=()):
    variables=dict(ProxyAddr=_text(proxy_addr),WorkerName='',DataDir=_text(data_dir),Now=now_string(now))
    body=render_system('worker',WORKER_DEFAULT,variables,override=override)
    return body+worker_traffic_block(bool(_text(ca_cert)))+artifact_spec(run_dir,worker=True)+constraint_block(constraints)


def render_intent_task(intent_id,payload):
    if type(intent_id) is not int or not 0<intent_id<2**63: raise ValueError('invalid intent identifier')
    return '\n\nAssigned intent (your ONLY task: execute only this one, produce facts, then stop):\n'+_json(payload)+'\nintent id: '+str(intent_id)+' (pass it to record_fact / report_finding).'


def render_worker_graph_overview(data):
    if not isinstance(data,dict): raise ValueError('overview must be a mapping')
    overview={key:value for key,value in data.items() if key!='coverage'}
    return '\n\nGlobal exploration overview (read-only awareness, not an expanded assignment): use this snapshot to avoid repeating existing work and understand how your intent relates to the whole task. Think broadly while investigating your assigned intent, but do not execute other intents; the planner assigns those to other workers. Persist valuable cross-asset leads, possible chain entries, and broader suspicions in a fact for the planner rather than discarding them.\n'+_json(overview)
