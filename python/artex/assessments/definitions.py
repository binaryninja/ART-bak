"""Executable assessment metadata. Discovery is never a scope grant."""
from dataclasses import dataclass
import re
from urllib.parse import urlsplit
from ..recon.servicenow import ALL_RESOURCES


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

    def public(self):
        return dict(key=self.key,version=self.version,name=self.name,operations=list(self.operations),access_levels=list(self.access_levels),max_intents=self.max_intents)


SERVICENOW=Definition('servicenow.inventory',1,'ServiceNow read-only assessment',ALL_RESOURCES,
    ('unauthenticated','authenticated_unknown','low_privilege','elevated','administrator'))
DEFINITIONS={SERVICENOW.key:SERVICENOW}


def capabilities(observations, username=''):
    current=observations.get('current_user',{})
    authenticated=current.get('status')==200 and any(r.get('user_name')==username for r in current.get('records',[]))
    roles=sorted({r.get('role.name','') for r in observations.get('roles',{}).get('records',[]) if r.get('role.name')})
    level='unauthenticated'
    if authenticated:
        level=('administrator' if 'admin' in roles else 'elevated' if any(r in roles for r in ('security_admin','itil_admin','app_engine_admin')) else 'low_privilege' if roles else 'authenticated_unknown')
    return dict(authenticated=authenticated,access_level=level,roles=roles,
                readable=[k for k,v in observations.items() if v.get('status')==200],
                limitations={k:v.get('error','HTTP '+str(v.get('status'))) for k,v in observations.items() if v.get('status')!=200})
