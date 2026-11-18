"""Executable assessment metadata. Discovery is never a scope grant."""
from dataclasses import dataclass
import re
from urllib.parse import urlsplit
from ..recon.servicenow import ALL_RESOURCES,TABLES,PAGES,DEFAULT_ENUMERATION

@dataclass(frozen=True)
class Check:
    key:str
    requires:str
    description:str
    def public(self,enumeration=DEFAULT_ENUMERATION):
        table=TABLES.get(self.key)
        return dict(key=self.key,requires=self.requires,description=self.description,method='GET',
            path=PAGES.get(self.key,'/api/now/table/'+table[0] if table else ''),
            evidence_fields=table[1].split(',') if table else ['status','body_sha256'],**(enumeration.public(self.key) if table else {'max_pages':1}))

_REQUIRES={'current_user':'credential','roles':'credential','application_scopes':'elevated',
    'delegated_developers':'elevated','integration_metadata':'elevated',
    'acl_definitions':'administrator','acl_roles':'administrator','role_inheritance':'administrator',
    'api_authentication':'administrator','security_properties':'administrator'}
SERVICENOW_CHECKS=tuple(Check(key,_REQUIRES.get(key,'public' if key in PAGES else 'authenticated'),key.replace('_',' ').capitalize()) for key in ALL_RESOURCES)
CHECKS={check.key:check for check in SERVICENOW_CHECKS}

def check_allowed(check,observed,has_credentials):
    if check.requires=='public':return True
    if check.requires=='credential':return bool(has_credentials)
    if check.requires=='authenticated':return observed.get('authenticated') is True
    if check.requires=='elevated':return observed.get('access_level') in ('elevated','administrator')
    return observed.get('access_level')=='administrator'


def instance_origin(value):
    if not isinstance(value,str) or len(value)>2048:raise ValueError('Invalid instance URL')
    p=urlsplit(value.strip())
    host=(p.hostname or '').lower()
    if p.scheme!='https' or p.username or p.password or p.port not in (None,443) or not re.fullmatch(r'[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?',host):
        raise ValueError('HTTPS instance URL required')
    return 'https://'+host


@dataclass(frozen=True)
class Definition:
    key:str
    version:int
    name:str
    operations:tuple
    access_levels:tuple
    max_intents:int=3

    def public(self,enumeration=DEFAULT_ENUMERATION):
        return dict(key=self.key,version=self.version,name=self.name,operations=list(self.operations),access_levels=list(self.access_levels),max_intents=self.max_intents,checks=[CHECKS[k].public(enumeration) for k in self.operations])


SERVICENOW=Definition('servicenow.inventory',3,'ServiceNow read-only assessment',ALL_RESOURCES,
    ('unauthenticated','authenticated_unknown','low_privilege','elevated','administrator'))
DEFINITIONS={SERVICENOW.key:SERVICENOW}


def capabilities(observations, username=''):
    current=observations.get('current_user',{})
    authenticated=current.get('status')==200 and any(r.get('user_name')==username for r in current.get('records',[]))
    roles=sorted({r.get('role.name','') for r in observations.get('roles',{}).get('records',[]) if r.get('role.name')})
    level='unauthenticated'
    if authenticated:
        level=('administrator' if 'admin' in roles else 'elevated' if any(r in roles for r in ('security_admin','itil_admin','app_engine_admin')) else 'low_privilege' if observations.get('roles',{}).get('status')==200 else 'authenticated_unknown')
    return dict(authenticated=authenticated,access_level=level,roles=roles,
                readable=[k for k,v in observations.items() if v.get('status')==200],
                limitations={k:v.get('error',v.get('pagination_error',str(v['source_unavailable_count'])+' script fields unavailable' if v.get('source_unavailable_count') else 'fields truncated' if v.get('fields_truncated') else 'bounded enumeration' if v.get('more_records_possible') else 'HTTP '+str(v.get('status')))) for k,v in observations.items() if v.get('status')!=200 or v.get('more_records_possible') or v.get('error') or v.get('fields_truncated') or v.get('source_unavailable_count')},
                roles_complete=observations.get('roles',{}).get('completeness')=='complete')
