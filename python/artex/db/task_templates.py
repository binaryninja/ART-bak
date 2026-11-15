"""Reusable task-template persistence from db/task_templates.go.

Global presets (as upstream); category FK references only, no profile/source-task
or execution binding. Atomic PATCH preserves omitted fields. Delete removes just
the preset, is permanent, and neither deletes nor alters tasks created from it.
No undo/archive copy is implied. Category deletion is schema ON DELETE SET NULL.
Rules are snapshots, not evaluated or executed here. Database schema/triggers
must already exist. All values are bound, including rule JSON.
"""
from dataclasses import dataclass,asdict
from datetime import datetime
import ipaddress,json,re
from .core import Query,int64

class TemplateInvalid(ValueError):pass
class TemplateConflict(RuntimeError):pass
class TemplateMissing(LookupError):pass

SPACE='\t\n\v\f\r \u0085\u00a0\u1680\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a\u2028\u2029\u202f\u205f\u3000'
COLS='id, name, nkey, description, goal, category_id, intercept_rules, created_at, updated_at'
FIELDS={'name','description','goal','category_id','intercept_rules'}


def identifier(value):
    if int64(value,'ID')<=0:raise TemplateInvalid('ID must be positive')
    return value


def text(value):
    if not isinstance(value,str) or '\x00' in value:raise TemplateInvalid('Invalid text')
    try:value.encode('utf-8')
    except UnicodeError as error:raise TemplateInvalid('Invalid UTF-8') from error
    return value


def rules(raw):
    if raw is None:return []
    if not isinstance(raw,list) or len(raw)>1000:raise TemplateInvalid('Invalid rules')
    result=[]
    for item in raw:
        if not isinstance(item,dict) or set(item)-{'enabled','action','kind','pattern','note'}:raise TemplateInvalid('Invalid rule fields')
        enabled=item.get('enabled',False)
        if type(enabled) is not bool:raise TemplateInvalid('Invalid enabled flag')
        action=text(item.get('action','')) or 'block'
        kind=text(item.get('kind',''));pattern=text(item.get('pattern','')).strip(SPACE);note=text(item.get('note',''))
        if action not in ('block','allow') or not pattern:raise TemplateInvalid('Invalid rule action/pattern')
        if kind=='exact_ip':
            try:
                if '%' in pattern:raise ValueError()
                ipaddress.ip_address(pattern)
            except ValueError as error:raise TemplateInvalid('Invalid exact IP') from error
        elif kind=='cidr':
            try:
                if '/' not in pattern or '%' in pattern or not re.fullmatch('[0-9]+',pattern.rsplit('/',1)[1]):raise ValueError()
                ipaddress.ip_network(pattern,strict=False)
            except ValueError as error:raise TemplateInvalid('Invalid CIDR') from error
        elif kind not in ('exact_domain','exact_url','fuzzy_domain','fuzzy_ip','fuzzy_url'):raise TemplateInvalid('Invalid rule kind')
        result.append(dict(enabled=enabled,action=action,kind=kind,pattern=pattern,note=note))
    return result


def normalize(data,*,patch=False):
    if not isinstance(data,dict) or set(data)-FIELDS or (patch and not data):raise TemplateInvalid('Invalid template fields')
    out={}
    for key in ('name','description','goal'):
        if patch and key not in data:continue
        value=text(data.get(key,''))
        value=' '.join(re.split('['+SPACE+']+',value.strip(SPACE))) if key=='name' else value.strip(SPACE)
        if not value or len(value)>(120 if key=='name' else 16000):raise TemplateInvalid('Invalid '+key)
        out[key]=value
    if 'category_id' in data or not patch:
        value=data.get('category_id')
        if value is not None:identifier(value)
        out['category_id']=value
    if 'intercept_rules' in data or not patch:out['intercept_rules']=rules(data.get('intercept_rules'))
    return out


def nkey(name):return ''.join(c.lower()[0] for c in name)


@dataclass
class TaskTemplate:
    id:int;name:str;nkey:str;description:str;goal:str;category_id:int|None;intercept_rules:list;created_at:datetime;updated_at:datetime
    def to_dict(self):
        values=asdict(self);values.pop('nkey')
        for key in ('created_at','updated_at'):values[key]=values[key].isoformat().replace('+00:00','Z')
        return values


def decode(row):
    values=list(row)
    if len(values)!=9:raise RuntimeError('Invalid template row')
    if isinstance(values[6],(str,bytes)):values[6]=json.loads(values[6])
    values[6]=values[6] or []
    return TaskTemplate(*values)


class TaskTemplateStore:
    def __init__(self,executor):self.executor=executor
    def _fetch(self,query):
        try:return self.executor.fetch_all(query)
        except Exception as error:
            state=getattr(error,'sqlstate',None) or getattr(error,'pgcode',None)
            if state=='23505':raise TemplateConflict('Template name already exists') from None
            if state=='23503':raise TemplateInvalid('Category does not exist') from None
            raise
    def list(self):return [decode(row) for row in self._fetch(Query('SELECT '+COLS+' FROM task_templates ORDER BY updated_at DESC, id DESC'))]
    def get(self,key):
        rows=self._fetch(Query('SELECT '+COLS+' FROM task_templates WHERE id=$1',(identifier(key),)))
        return decode(rows[0]) if rows else None
    def create(self,data):
        data=normalize(data)
        rows=self._fetch(Query('INSERT INTO task_templates(name,nkey,description,goal,category_id,intercept_rules) VALUES ($1,$2,$3,$4,$5,$6) ON CONFLICT (nkey) DO NOTHING RETURNING '+COLS,
            (data['name'],nkey(data['name']),data['description'],data['goal'],data['category_id'],json.dumps(data['intercept_rules']))))
        if not rows:raise TemplateConflict('Template name already exists')
        return decode(rows[0])
    def patch(self,key,data):
        identifier(key);data=normalize(data,patch=True)
        rows=self._fetch(Query('''UPDATE task_templates
SET name=CASE WHEN $2 THEN $3::text ELSE name END,
    nkey=CASE WHEN $2 THEN $4::text ELSE nkey END,
    description=CASE WHEN $5 THEN $6::text ELSE description END,
    goal=CASE WHEN $7 THEN $8::text ELSE goal END,
    category_id=CASE WHEN $9 THEN $10::bigint ELSE category_id END,
    intercept_rules=CASE WHEN $11 THEN $12::jsonb ELSE intercept_rules END
WHERE id=$1 RETURNING '''+COLS,
            (key,'name' in data,data.get('name'),nkey(data['name']) if 'name' in data else None,
             'description' in data,data.get('description'),'goal' in data,data.get('goal'),
             'category_id' in data,data.get('category_id'),'intercept_rules' in data,json.dumps(data.get('intercept_rules',[])))))
        if not rows:raise TemplateMissing('Task template not found')
        return decode(rows[0])
    def delete(self,key):
        count=self.executor.execute(Query('DELETE FROM task_templates WHERE id=$1',(identifier(key),)))
        if type(count) is not int or count not in (0,1):raise RuntimeError('Invalid delete result')
        return count==1
