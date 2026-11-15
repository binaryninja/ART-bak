"""Non-executing subset of agent/prompt.go's text/template renderer.

Source registers NO custom FuncMap. Its builtins use {{.Field}}; source tests
also require {{if .ProxyAddr}}...{{else}}...{{end}}. These forms, nested if,
Go ASCII whitespace trimming ({{- action -}}), and template comments are
supported. Values are catalog string fields, inserted verbatim without HTML
escaping or recursive template expansion, as Go text/template does.

Unsupported: functions/pipelines (including Go standard builtins), range/with,
define/template/block, assignments, dotted traversal, methods, literal actions,
else-if, custom delimiters. They fail explicitly, including in unselected arms.
There is no eval, arbitrary attribute access, iteration or template execution.
Parse depth/actions/input/output are bounded. Malformed/missing values never
produce partial output. render_body can fall back wholly to the code-owned body
on override syntax failure, but unlike Go never returns an unrendered default.

English builtin seed mapping includes mainagent/worker/planner/goals/auto/pentest
EDITABLE bodies. Reporter is a separate custom-agent seed, never a builtin.
render_chat_body mirrors chatSystem: missing/empty/invalid stored templates use
the generic assistant fallback EVEN for builtin keys such as pentest/auto or
reporter. Seeds are not runtime fallback selection. No database seeding,
runtime installation or default permissions.
Code-owned traffic/artifact/constraint tails MUST still be appended by the host's
existing builders; this renderer does not allow editable bodies to replace them.
"""
from dataclasses import dataclass
import re
from .prompts import MAIN_DEFAULT, WORKER_DEFAULT
from .planner_prompts import PLANNER_DEFAULT, GOALS_DEFAULT, AUTO_DEFAULT
from .additional_prompts import PENTEST_DEFAULT, REPORTER_DEFAULT_PROMPT, DEFAULT_ASSISTANT_PROMPT


class PromptRenderError(ValueError):
    pass


@dataclass(frozen=True)
class RenderLimits:
    max_template_bytes:int=1048576
    max_output_bytes:int=1048576
    max_actions:int=10000
    max_depth:int=32
    def __post_init__(self):
        if any(type(v) is not int or v<=0 for v in vars(self).values()):
            raise ValueError('Positive rendering bounds required')


CATALOG={
    'mainagent':frozenset(('Goal','AssetSummary','FindingsSummary','DataDir','Now')),
    'worker':frozenset(('ProxyAddr','WorkerName','DataDir','Now')),
    'planner':frozenset(('Goal','Scope','AssetSummary','DataDir','Now')),
    'goals':frozenset(('EngagementDescription','DataDir','Now')),
    'auto':frozenset(('DataDir','Now')),
    'custom':frozenset(('DataDir','Now')),
    'pentest':frozenset(('DataDir','Now')),
    'reporter':frozenset(('DataDir','Now')),
}
_SEEDS={'mainagent':MAIN_DEFAULT,'worker':WORKER_DEFAULT,'planner':PLANNER_DEFAULT,
        'goals':GOALS_DEFAULT,'auto':AUTO_DEFAULT,'pentest':PENTEST_DEFAULT}
_FIELD=re.compile(r'\.([A-Za-z][A-Za-z0-9]*)\Z')
_WS=' \t\r\n'


def _text(value):
    if not isinstance(value,str) or '\x00' in value:
        raise PromptRenderError('Prompt input must be text without NUL')
    try:value.encode('utf-8')
    except UnicodeError:raise PromptRenderError('Prompt input must be valid UTF-8') from None
    return value


def builtin_templates():
    """Explicit English seed candidates, not a seeding operation."""
    return dict(_SEEDS)


def render(template,variables,*,role,limits=RenderLimits()):
    _text(template)
    if not isinstance(limits,RenderLimits):raise PromptRenderError('Invalid render limits')
    if role not in CATALOG or type(variables) is not dict or set(variables)-CATALOG[role]:
        raise PromptRenderError('Unknown role or variable')
    for value in variables.values():_text(value)
    if len(template.encode('utf-8'))>limits.max_template_bytes:
        raise PromptRenderError('Template exceeds byte bound')
    # Parse all arms before output: unsupported syntax never hides in a branch.
    root=[];current=root;stack=[];position=0;trim_next=False;actions=0
    while position<len(template):
        start=template.find('{{',position)
        endtext=len(template) if start<0 else start
        literal=template[position:endtext]
        if trim_next:literal=literal.lstrip(_WS);trim_next=False
        if start<0:
            if literal:current.append(('text',literal))
            break
        action_start=start+2
        left_trim=template[action_start:action_start+1]=='-' and template[action_start+1:action_start+2] in _WS and bool(template[action_start+1:action_start+2])
        if left_trim:
            literal=literal.rstrip(_WS);action_start+=2
        if literal:current.append(('text',literal))
        probe=action_start
        if template[probe:].lstrip(_WS).startswith('/*') and not template.startswith('/*',probe):
            raise PromptRenderError('Malformed comment opening delimiter')
        if template.startswith('/*',probe):
            comment_end=template.find('*/',probe+2)
            if comment_end<0:raise PromptRenderError('Unclosed template comment')
            close=template.find('}}',comment_end+2)
            suffix=template[comment_end+2:close]
            if close<0 or not (suffix=='' or (len(suffix)==2 and suffix[0] in _WS and suffix[1]=='-')):
                raise PromptRenderError('Malformed template comment')
        else:
            close=template.find('}}',action_start)
            if close<0:raise PromptRenderError('Unclosed template action')
        body=template[action_start:close]
        right_trim=len(body)>=2 and body[-1]=='-' and body[-2] in _WS
        if right_trim:body=body[:-1];trim_next=True
        body=body.strip(_WS)
        position=close+2;actions+=1
        if actions>limits.max_actions:raise PromptRenderError('Too many template actions')
        if body.startswith('/*') and body.endswith('*/'):continue
        if re.match(r'if[ \t\r\n]',body):
            match=_FIELD.fullmatch(body[2:].strip(_WS))
            if not match:raise PromptRenderError('Unsupported if expression')
            name=match.group(1)
            if name not in CATALOG[role] or name not in variables:raise PromptRenderError('Missing or unknown variable')
            if len(stack)>=limits.max_depth:raise PromptRenderError('Template nesting exceeds bound')
            node=['if',name,[],[]];current.append(node);stack.append((current,node,False));current=node[2]
        elif body=='else':
            if not stack or stack[-1][2]:raise PromptRenderError('Unexpected or duplicate else')
            parent,node,_=stack[-1];stack[-1]=(parent,node,True);current=node[3]
        elif body=='end':
            if not stack:raise PromptRenderError('Unexpected end')
            current,_,_=stack.pop()
        else:
            match=_FIELD.fullmatch(body)
            if not match:raise PromptRenderError('Unsupported template action')
            name=match.group(1)
            if name not in CATALOG[role] or name not in variables:raise PromptRenderError('Missing or unknown variable')
            current.append(('value',name))
    if stack:raise PromptRenderError('Unclosed if action')
    chunks=[];size=0
    def emit(nodes):
        nonlocal size
        for node in nodes:
            if node[0]=='if':emit(node[2] if variables[node[1]] else node[3]);continue
            value=node[1] if node[0]=='text' else variables[node[1]]
            size+=len(value.encode('utf-8'))
            if size>limits.max_output_bytes:raise PromptRenderError('Rendered output exceeds byte bound')
            chunks.append(value)
    emit(root)
    return ''.join(chunks)


def render_body(role,variables,*,override=None,limits=RenderLimits()):
    """Code-owned default fallback for supported builtin EDITABLE bodies only."""
    if role not in _SEEDS:raise PromptRenderError('Builtin role has no reviewed English default')
    if override is not None:
        _text(override)
        if override:
            try:return render(override,variables,role=role,limits=limits)
            except PromptRenderError:pass
    return render(_SEEDS[role],variables,role=role,limits=limits)


def custom_seed_templates():
    """Explicit named custom-agent seeds; reporter retains its custom identity."""
    return {'reporter':REPORTER_DEFAULT_PROMPT}


def custom_default_template():
    """Source custom-agent creation seed and chatSystem fallback, not an agent key."""
    return DEFAULT_ASSISTANT_PROMPT


def render_chat_body(agent_key,variables,*,stored_template=None,limits=RenderLimits()):
    """Source chatSystem body: DB template or generic fallback, for EVERY key.

    agent_key identifies an already-resolved host catalog entry; it is not used
    to select a privileged/builtin prompt. The caller performs authorization and
    appends its immutable chatWorkDirSpec. No lookup or seed occurs here.
    """
    _text(agent_key)
    if not agent_key or len(agent_key.encode('utf-8'))>128:
        raise PromptRenderError('Explicit bounded agent identity required')
    if stored_template is not None:
        _text(stored_template)
        if stored_template:
            try:return render(stored_template,variables,role='custom',limits=limits)
            except PromptRenderError:pass
    return render(DEFAULT_ASSISTANT_PROMPT,variables,role='custom',limits=limits)
