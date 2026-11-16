"""Unregistered GET /api/chat/mentions. Catalog metadata is not execution authority."""
import re
from dataclasses import dataclass,field
from weakref import WeakKeyDictionary
from urllib.parse import urlsplit,parse_qsl
from ..db.chat_mentions import ChatMentionReader,MentionSelection,MentionInvalid
from ..db.judge_config_read import SPACE
from .core import json_response,error_response

def parse_request(request):
    if request.method!='GET' or request.path!='/api/chat/mentions' or request.body:raise MentionInvalid()
    query=urlsplit(request.target).query
    if len(query)>8192 or re.search(r'%(?![0-9A-Fa-f]{2})',query):raise MentionInvalid()
    values={}
    try:
        for k,v in parse_qsl(query,keep_blank_values=True,strict_parsing=True,errors='strict',max_num_fields=3):
            if k not in ('kind','q','cursor') or k in values:raise MentionInvalid()
            values[k]=v
        result=MentionSelection(values.get('kind',''),values.get('q','').strip(SPACE),values.get('cursor',''))
        result.validate();return result
    except Exception:raise MentionInvalid('Invalid catalog search') from None

@dataclass(frozen=True,eq=False)
class PreparedMentionRead:
    response:object=field(repr=False)
    selection:object=field(repr=False)
    asset_ids:tuple=field(repr=False)

class ChatMentionService:
    def __init__(self,reader,*,authorize):
        if type(reader) is not ChatMentionReader or not callable(authorize):raise ValueError('Exact global catalog reader/request grant required')
        reader.validate();self.reader=reader;self.authorize=authorize
        self._pins=(reader,reader.executor,reader.scope,authorize)
        self._issued=WeakKeyDictionary();self._issued_pin=self._issued
    def _check(self):
        r,e,s,a=self._pins
        if self.reader is not r or r.executor is not e or r.scope is not s or self.authorize is not a:raise PermissionError()
        r.validate()
        if self._issued is not self._issued_pin:raise PermissionError()
    def _issue(self,response,selection,ids,validate):
        receipt=PreparedMentionRead(response,selection,ids)
        identity=None if selection is None else (selection.kind,selection.query,selection.cursor)
        self._issued_pin[receipt]=(response,selection,ids,identity,response.status,response.body,tuple(response.headers.items()),validate)
        return receipt
    def check_prepared(self,receipt):
        """Pure issued-identity check; never invokes a disclosure policy."""
        self._check()
        if type(receipt) is not PreparedMentionRead or receipt not in self._issued_pin:raise PermissionError()
        response,selection,ids,identity,status,body,headers,validate=self._issued_pin[receipt]
        if receipt.response is not response or receipt.selection is not selection or receipt.asset_ids is not ids or response.status!=status or type(response.status) is not int or response.body is not body or tuple(response.headers.items())!=headers:raise PermissionError()
        if selection is not None and (type(selection) is not MentionSelection or (selection.kind,selection.query,selection.cursor)!=identity):raise PermissionError()
    def validate_prepared(self,receipt):
        self.check_prepared(receipt)
        validate=self._issued_pin[receipt][-1]
        if validate is not None:validate()
        self.check_prepared(receipt)
    def handle(self,request):
        return self.prepare(request).response
    def prepare(self,request,*,disclosure=None):
        try:
            if disclosure is not None and not callable(disclosure):raise MentionInvalid()
            selection=parse_request(request);identity=(selection.kind,selection.query,selection.cursor)
            original=(request.method,request.target,request.body,tuple(request.headers.items()))
            def allow():
                self._check()
                if original!=(request.method,request.target,request.body,tuple(request.headers.items())) or identity!=(selection.kind,selection.query,selection.cursor):raise PermissionError()
                granted=self._pins[3](request,self._pins[2],selection)
                self._check()
                if granted is not True:return False
                if disclosure is not None:
                    if disclosure(request,self._pins[2],selection) is not True:return False
                    self._check()
                    granted=self._pins[3](request,self._pins[2],selection)
                    self._check()
                if original!=(request.method,request.target,request.body,tuple(request.headers.items())) or identity!=(selection.kind,selection.query,selection.cursor):raise PermissionError()
                return granted is True
            result,ids=self._pins[0].read(selection,disclosure=allow)
            response=json_response(200,result)
            self._pins[0].validate_disclosure(selection,ids,disclosure=allow)
            self._check();return self._issue(response,selection,ids,lambda:self._pins[0].validate_disclosure(selection,ids,disclosure=allow))
        except MentionInvalid:return self._issue(error_response(400,'Invalid catalog search or cursor'),None,(),None)
        except PermissionError:return self._issue(error_response(403,'Catalog search denied'),None,(),None)
        except Exception:return self._issue(error_response(500,'Catalog search unavailable'),None,(),None)
