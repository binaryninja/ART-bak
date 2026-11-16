"""Unregistered GET /api/chat/mentions. Catalog metadata is not execution authority."""
import re
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

class ChatMentionService:
    def __init__(self,reader,*,authorize):
        if type(reader) is not ChatMentionReader or not callable(authorize):raise ValueError('Exact global catalog reader/request grant required')
        reader.validate();self.reader=reader;self.authorize=authorize
        self._pins=(reader,reader.executor,reader.scope,authorize)
    def _check(self):
        r,e,s,a=self._pins
        if self.reader is not r or r.executor is not e or r.scope is not s or self.authorize is not a:raise PermissionError()
        r.validate()
    def handle(self,request):
        try:
            selection=parse_request(request);identity=(selection.kind,selection.query,selection.cursor)
            original=(request.method,request.target,request.body,tuple(request.headers.items()))
            def allow():
                self._check()
                if original!=(request.method,request.target,request.body,tuple(request.headers.items())) or identity!=(selection.kind,selection.query,selection.cursor):raise PermissionError()
                granted=self._pins[3](request,self._pins[2],selection)
                self._check()
                if original!=(request.method,request.target,request.body,tuple(request.headers.items())) or identity!=(selection.kind,selection.query,selection.cursor):raise PermissionError()
                return granted is True
            result,ids=self._pins[0].read(selection,disclosure=allow)
            response=json_response(200,result)
            self._pins[0].validate_disclosure(selection,ids,disclosure=allow)
            self._check();return response
        except MentionInvalid:return error_response(400,'Invalid catalog search or cursor')
        except PermissionError:return error_response(403,'Catalog search denied')
        except Exception:return error_response(500,'Catalog search unavailable')
