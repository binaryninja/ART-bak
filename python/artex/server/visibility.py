"""Four opt-in source resource/skill visibility endpoints, no default wiring.

Source agent_id is decimal string; MCP resource_id numeric. Omitted visible
means false as in Go; explicit values must be bool. Read returns string IDs.
Capability-selection metadata can affect later discovery, but never bypasses
Python tool/skill permission checks. No runtime reload, seeding or connections.
"""
import json
from urllib.parse import urlsplit
from .core import json_response,error_response
from .task_goals import _id,_pairs
from ..db.visibility import VisibilityStore,VisibilitySelection,VisibilityDenied,VisibilityUnavailable,VisibilityMissing,VisibilityUncertain
class VisibilityService:
    def __init__(self,store,*,authorize):
        if type(store) is not VisibilityStore or not callable(authorize):raise ValueError('Actual scoped store and request policy required')
        self.store=store;self._store=store;self._executor=store.executor;self.authorize=authorize
        self._scope=store.scope;self._guard=store.guard
    def _check_binding(self):
        store=self.store
        if (store is not self._store or store.executor is not self._executor
                or store._executor is not self._executor
                or store.scope is not self._scope or store._scope is not self._scope
                or store.guard is not self._guard or store._guard is not self._guard):
            raise VisibilityUnavailable()
    def register(self,router):
        router.add('GET','/api/visibility/{kind}/{id}',lambda req,p:self.handle('resource_read',req,p))
        router.add('POST','/api/visibility/toggle',lambda req,p:self.handle('resource_toggle',req,p))
        router.add('GET','/api/visibility/skill/{name}',lambda req,p:self.handle('skill_read',req,p))
        router.add('POST','/api/visibility/skill/toggle',lambda req,p:self.handle('skill_toggle',req,p))
    def handle(self,action,request,params):
        writing=action.endswith('_toggle');attempted=False
        try:
            if urlsplit(request.target).query or len(request.body)>8192:raise ValueError()
            if writing:
                data=json.loads(request.body.decode(),object_pairs_hook=_pairs)
                allowed={'agent_id','visible','skill_name'} if action=='skill_toggle' else {'agent_id','visible','kind','resource_id'}
                if type(data) is not dict or set(data)-allowed:raise ValueError()
                agent=_id(data.get('agent_id'));visible=data.get('visible',False)
                p=VisibilitySelection('skill',skill_name=data.get('skill_name'),agent_id=agent,visible=visible) if action=='skill_toggle' else VisibilitySelection(data.get('kind'),data.get('resource_id'),agent_id=agent,visible=visible)
            else:
                if request.body:raise ValueError()
                p=VisibilitySelection('skill',skill_name=params['name']) if action=='skill_read' else VisibilitySelection(params['kind'],_id(params['id']))
            self._check_binding()
            def policy(scope,operation,proposal):
                self._check_binding()
                allowed=self.authorize(request,scope,operation,proposal)
                self._check_binding()
                return allowed
            if writing:
                attempted=True;self.store.toggle(p,request_authorize=policy);return json_response(200,{'ok':True})
            return json_response(200,{'agents':[str(i) for i in self.store.read(p,request_authorize=policy)]})
        except VisibilityUncertain:return json_response(500,{'error':'Visibility mutation outcome requires reconciliation','outcome':'unknown','retry':False})
        except VisibilityDenied:return error_response(403,'Visibility access denied')
        except VisibilityMissing:return error_response(404,'Visibility agent or resource not found')
        except VisibilityUnavailable:return error_response(503,'Visibility capability unavailable; reconcile mutations before retrying')
        except (ValueError,TypeError,UnicodeError):return error_response(400,'Invalid visibility request')
        except Exception:return error_response(500,'Visibility mutation outcome requires reconciliation' if attempted else 'Visibility read failed')
