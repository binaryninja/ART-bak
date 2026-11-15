"""Pure translated source wrap-up policies; NEVER an execution/budget adapter.

agent/wrapup.go plus worker.go settleWrapUpPrompt: editable prompt body and
positive turn override; tool exclusions are code-owned. Worker excludes Bash;
planner/main/chat source excludes none. Prompt wording does not itself enforce
other tool prohibitions. Hosts may add exclusions, never remove worker Bash.
No global override callbacks, implicit database reads, provider calls or automatic
extra turns. Resolve freshly per authorized run, then integrate only within an
explicit total budget. Current engine rejects deadlines; normative settlement
reason mapping, durable final rounds and accounting remain separate/unimplemented.

Source timeout policy sets timeout-specific turn count for BOTH reasons when
clamped and a timeout prompt exists, although max_turns uses per-run wording.
This unintuitive detail is preserved, not silently 'fixed'. Overrides are bounded
(1 MiB text, max 10000 turns), stricter than source unbounded positive ints.
"""
from dataclasses import dataclass
import re

WORKER_DEFAULT='You are about to be stopped because your budget is exhausted. Do not run any more commands or probes. In order: (1) Persist each result you already identified but have not written back: new assets with insert_assets, exploration conclusions/facts with record_fact, and confirmed vulnerabilities with report_finding. (2) End with one separate plain-text sentence summarizing what you did and the key conclusions. This sentence is displayed as the result of this run, so you must provide it.'
PLANNER_DEFAULT='This planning round is about to exhaust its steps. Only this round is ending: the system will wake you again as the situation changes; the task is not ending and you do not need to wind down the entire plan. Persist conclusions already reached so this round is not wasted, but do not invent intents just to wrap up; zero intents is a valid result. (1) If you identified exploration directions that should be dispatched now, submit them together with add_intent. (2) Use prove_goal to mark goals met when findings or facts already prove them; do not overlook achieved goals. (3) Record serial exploitation chains that need multiple steps in TodoWrite so the next wake can continue dispatching them. Then end this round without a summary.'
MAINAGENT_DEFAULT='Your steps are about to run out and this interaction is ending. Do not initiate new exploration or operations. Give the user one separate plain-text sentence summarizing progress, key conclusions, and the recommended next step.'
GENERIC_DEFAULT='You are about to be stopped because your budget is exhausted. First persist completed results that have not yet been written back, then give one separate plain-text sentence summarizing what you did and the key conclusions. This sentence is displayed as the result of this run.'
WORKER_TASK_TIMEOUT='The entire task has reached its timeout and is ending. This is not merely the budget for your current run; the whole exploration is out of time. This is the last opportunity: (1) Persist ALL results already identified but not written back: new assets with insert_assets, exploration conclusions/facts with record_fact, and confirmed vulnerabilities with report_finding. (2) Do not start any new commands or probes. (3) Finish with one separate plain-text sentence summarizing your key conclusions for this intent.'
PLANNER_TASK_TIMEOUT='The entire task has reached its timeout and is ending, not just this round. Using ALL current facts and findings, make the final goal assessment: use prove_goal to mark goals met where evidence proves them, without overlooking achieved goals. Do not generate any new intents; they will not be executed now. Finish after the assessment, without a summary.'
_DEFAULTS={'worker':WORKER_DEFAULT,'planner':PLANNER_DEFAULT,'mainagent':MAINAGENT_DEFAULT}
_TIMEOUT={'worker':WORKER_TASK_TIMEOUT,'planner':PLANNER_TASK_TIMEOUT}

@dataclass(frozen=True)
class Overrides:
    prompt:object=None
    turns:object=None
    task_timeout_prompt:object=None
    task_timeout_turns:object=None
    def __post_init__(self):
        if any(value is not None and not callable(value) for value in (self.prompt,self.turns,self.task_timeout_prompt,self.task_timeout_turns)):raise ValueError('Override readers must be explicit callables')

@dataclass(frozen=True)
class SettlementPolicy:
    prompt:str
    disabled_tools:tuple[str,...]
    max_turns:int
    prompt_by_reason:tuple[tuple[str,str],...]=()
    def prompt_for(self,reason):
        if not isinstance(reason,str):raise ValueError('Reason must be text')
        return dict(self.prompt_by_reason).get(reason,self.prompt)


def _key(key):
    if not isinstance(key,str) or not re.fullmatch(r'[A-Za-z][A-Za-z0-9_-]{0,127}',key):raise ValueError('Explicit bounded agent key required')
    return key

def wrapup_default(agent_key):return _DEFAULTS.get(_key(agent_key),GENERIC_DEFAULT)
def wrapup_turns_default(agent_key):_key(agent_key);return 10
def task_timeout_wrapup_default(agent_key):return _TIMEOUT.get(_key(agent_key),'')

def _resolve(reader,key,default,kind):
    if reader is None:return default
    result=reader(key)
    if not isinstance(result,tuple) or len(result)!=2 or type(result[1]) is not bool:raise ValueError('Override must return (value, found)')
    value,found=result
    if kind=='prompt':
        if not isinstance(value,str) or '\0' in value or len(value.encode('utf-8'))>1048576:raise ValueError('Invalid bounded wrap-up prompt')
        return value if found and value.strip() else default
    if type(value) is not int or not -(2**63)<=value<2**63 or value>10000:raise ValueError('Invalid bounded wrap-up turn count')
    return value if found and value>0 else default

def resolve_wrapup(agent_key,overrides=Overrides()):
    return _resolve(overrides.prompt,_key(agent_key),wrapup_default(agent_key),'prompt')
def resolve_wrapup_turns(agent_key,overrides=Overrides()):
    return _resolve(overrides.turns,_key(agent_key),wrapup_turns_default(agent_key),'turns')
def resolve_task_timeout_wrapup(agent_key,overrides=Overrides()):
    return _resolve(overrides.task_timeout_prompt,_key(agent_key),task_timeout_wrapup_default(agent_key),'prompt')
def resolve_task_timeout_turns(agent_key,overrides=Overrides()):
    # Resolve fallback lazily only if needed, like source live override readers.
    value=_resolve(overrides.task_timeout_turns,_key(agent_key),0,'turns')
    return value or resolve_wrapup_turns(agent_key,overrides)

def settlement_for_task(agent_key,*,clamped=False,disabled_tools=(),overrides=Overrides()):
    _key(agent_key)
    if type(clamped) is not bool or not isinstance(overrides,Overrides):raise ValueError('Explicit clamp flag and override readers required')
    if not isinstance(disabled_tools,(tuple,list)) or len(disabled_tools)>1000:raise ValueError('Bounded tool exclusions required')
    exclusions=['Bash'] if agent_key=='worker' else []
    for name in disabled_tools:
        if not isinstance(name,str) or not re.fullmatch(r'[A-Za-z][A-Za-z0-9_.:-]{0,127}',name):raise ValueError('Invalid excluded tool name')
        if name not in exclusions:exclusions.append(name)
    prompt=resolve_wrapup(agent_key,overrides);turns=resolve_wrapup_turns(agent_key,overrides);reasons=()
    if clamped:
        timeout=resolve_task_timeout_wrapup(agent_key,overrides)
        if timeout:
            reasons=(('timeout',timeout),('max_turns',prompt));turns=resolve_task_timeout_turns(agent_key,overrides)
    return SettlementPolicy(prompt,tuple(exclusions),turns,reasons)

def wrapup_settlement(agent_key,*,disabled_tools=(),overrides=Overrides()):
    return settlement_for_task(agent_key,disabled_tools=disabled_tools,overrides=overrides)
