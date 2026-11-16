import io,json,unittest
from contextlib import nullcontext
from copy import deepcopy
from artex.db.archive_catalog import ArchiveCatalogReader,ArchiveCatalogUnavailable,decode,_SQL
from artex.server.archive_catalog import ArchiveCatalogService,build_archive_catalog_application
from artex.server.application import build_application
from artex.server.auth import sign_jwt
from artex.server.runtime_listener import _AuthenticatedView
from test_application import TestCodec

STAMP='2026-11-16T00:00:00+00:00'
def item():
    return dict(id=1,task_id=5,state='archive_failed',phase='failed',progress=10,format_version=3,
        sha256='',original_size=12,compressed_size=3,task_name='title',task_description='description',task_goal='goal',
        original_status='done',source_task_ids=[6],remaining_timeout_seconds=4,data_counts={'tasks':1},
        archived_at=None,requested_at=STAMP,created_at=STAMP,updated_at=STAMP,
        category_id=None,category_name='',aggregate_stats={'tokens':{'calls':2},'skills':{'safe-skill':1}})
class DB:
    def __init__(self):self.rows=[(1,json.dumps(item()))];self.queries=[];self.hook=lambda:None
    def fetch_all(self,q):self.queries.append(q);self.hook();return self.rows

class ReaderTests(unittest.TestCase):
    def test_projection_pagination_and_empty_total(self):
        db=DB();reader=ArchiveCatalogReader(db)
        result=reader.read();self.assertEqual(result['items'][0]['state'],'archive_failed');self.assertEqual(result['total'],1)
        self.assertNotIn('error',result['items'][0]);self.assertNotIn('warnings',result['items'][0])
        self.assertNotIn('archive_path',_SQL);self.assertNotIn('a.error',_SQL);self.assertNotIn('a.warnings',_SQL)
        db.rows=[(12,None)];self.assertEqual(reader.read(page=9,size=2),dict(items=[],total=12,page=9,size=2))
        db.rows=[(0,None)];self.assertIsNone(reader.read(identifier=9))
    def test_invalid_projection_refuses_not_redacted_fake_values(self):
        cases=[]
        for k,v in [('phase','/private/path'),('progress',101),('task_id',True),('data_counts',{'x':-1}),
                    ('aggregate_stats',{'diagnostic':{'secret':1}}),('requested_at','2026-11-16'),('source_task_ids',[True]),
                    ('task_goal','x'*16385)]:
            data=item();data[k]=v;cases.append(data)
        for data in cases:
            with self.subTest(data=data),self.assertRaises(ArchiveCatalogUnavailable):decode(json.dumps(data))
        with self.assertRaises(ArchiveCatalogUnavailable):decode('{"id":1,"id":2}')
    def test_overflow_partial_duplicate_and_mismatch_refuse(self):
        db=DB();reader=ArchiveCatalogReader(db)
        for rows in ([(1,None)],[(2,json.dumps(item()))],[(2,json.dumps(item())),(2,json.dumps(item()))]):
            db.rows=rows
            with self.assertRaises(ArchiveCatalogUnavailable):reader.read()
        db.rows=[(1,json.dumps(item()))]
        with self.assertRaises(ArchiveCatalogUnavailable):reader.read(identifier=2)

class CompositionTests(unittest.TestCase):
    def setUp(self):
        self.db=DB();self.source=self.request=True;self.now=1000.
        self.service=ArchiveCatalogService(self.db,workspace='fixture',authorize=lambda w:self.source,
            request_authorize=lambda r,w:self.request,lifecycle_guard=lambda w:nullcontext())
        self.base=build_application(self.db,b'x'*32,TestCodec());self.base.router.authorize.__self__.clock=lambda:self.now
        self.app=build_archive_catalog_application(self.base,self.service)
    def call(self,path='/api/task-archives',query='',headers=lambda:None,method='GET'):
        statuses=[];env={'REQUEST_METHOD':method,'PATH_INFO':path,'QUERY_STRING':query,'CONTENT_LENGTH':'0',
            'HTTP_AUTHORIZATION':'Bearer '+sign_jwt(b'x'*32,now=1000.),'wsgi.input':io.BytesIO()}
        def start(status,h):statuses.append(status);headers()
        body=b''.join(_AuthenticatedView(self.app,lambda:None)(env,start))
        return int(statuses[0].split()[0]),body
    def test_list_detail_and_no_actions(self):
        status,raw=self.call(query='page=1&size=20&q=title&state=archive_failed')
        self.assertEqual(status,200);self.assertEqual(json.loads(raw)['items'][0]['task_id'],5)
        self.assertEqual(self.call('/api/task-archives/1')[0],200)
        self.db.rows=[(0,None)];self.assertEqual(self.call('/api/task-archives/2')[0],404)
        self.assertEqual(self.call('/api/task-archives/1/restore',method='POST')[0],404)
    def test_independent_denial_before_sql_and_bad_queries(self):
        for k in ('source','request'):
            setattr(self,k,False);self.assertEqual(self.call()[0],403);self.assertEqual(self.db.queries,[]);setattr(self,k,True)
        for q in ('page=1&page=2','state=unknown','q='+('x'*513),'size=bad','task=5'):
            self.assertEqual(self.call(query=q)[0],400)
        self.assertEqual(self.db.queries,[])
    def test_postheader_grants_jwt_request_and_binding(self):
        for k in ('source','request','jwt','binding'):
            self.source=self.request=True;self.now=1000.;self.service._pins=self.service._original
            def revoke():
                if k=='jwt':self.now=10**10
                elif k=='binding':self.service._pins=tuple(list(self.service._original))
                else:setattr(self,k,False)
            before=len(self.db.queries);status,body=self.call(headers=revoke)
            self.assertEqual(status,200);self.assertEqual(body,b'');self.assertEqual(len(self.db.queries)-before,1)
    def test_failure_path_reauthorizes(self):
        for rows in ([(1,None)],[(0,None)],[]):
            for k in ('source','request','jwt'):
                self.source=self.request=True;self.now=1000.;self.db.rows=rows
                self.db.hook=lambda:setattr(self,'now' if k=='jwt' else k,10**10 if k=='jwt' else False)
                self.assertEqual(self.call()[0],403)
    def test_conflict_refuses_without_original_mutation(self):
        self.base.router.add('GET','/api/task-archives',lambda *a:None);before=tuple(self.base.router._routes)
        with self.assertRaises(ValueError):build_archive_catalog_application(self.base,self.service)
        self.assertEqual(tuple(self.base.router._routes),before)

    def test_parameter_route_conflicts_without_mutation(self):
        for method,path in [('GET','/api/{resource}'),('HEAD','/api/{kind}/{id}'),('GET','/{prefix}/task-archives/{id}')]:
            base=build_application(self.db,b'x'*32,TestCodec())
            base.router.add(method,path,lambda *a:None);before=tuple(base.router._routes)
            with self.assertRaises(ValueError):build_archive_catalog_application(base,self.service)
            self.assertEqual(tuple(base.router._routes),before)
    def test_original_jwt_resources_pinned_after_headers(self):
        owner=self.base.router.authorize.__self__
        for name in ('clock','key','store','codec','authorized'):
            original=getattr(owner,name)
            def replace():
                self.now=10**10
                setattr(owner,name,(lambda:1000.) if name=='clock' else (lambda r:True) if name=='authorized' else object())
            self.now=1000.
            status,body=self.call(headers=replace)
            self.assertEqual(status,200);self.assertEqual(body,b'')
            if name=='authorized':del owner.authorized
            else:setattr(owner,name,original)

    def test_deeper_parameter_route_preserves_original_dispatch(self):
        from artex.server.core import json_response
        self.base.router.add('GET','/api/{kind}/{id}/events',lambda req,p:json_response(200,{'original':p['id']}))
        self.app=build_archive_catalog_application(self.base,self.service)
        status,body=self.call('/api/task-archives/1/events')
        self.assertEqual(status,200);self.assertEqual(json.loads(body),{'original':'1'})
        self.assertEqual(self.db.queries,[])
