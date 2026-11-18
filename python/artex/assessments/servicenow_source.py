"""Source contracts, call-graph evidence and instance-backed REST test cases."""
import base64,hashlib,hmac,json,re,uuid

ZERO='0'*32
FIELDS='sys_id,name,http_method,operation_uri,default_operation_uri,active,requires_authentication,operation_script,request_example,consumes,produces,short_description'
HELPERS=('sn_sc.RestCatalogUtil','sn_km_portal.KBPortalService','global.KBPortalServiceImpl','sn_doc_services.DocumentPermalinkAPI','sn_one_extend.GenerativeAIUtility')

def sha(text):return hashlib.sha256(text.encode()).hexdigest()
def signature(runtime,value):return hmac.new(runtime._assessment_plan_key,json.dumps({k:v for k,v in value.items() if k!='plan_signature'},sort_keys=True,separators=(',',':')).encode(),hashlib.sha256).hexdigest()
def verified(runtime,record,value):
    return isinstance(value,dict) and value.get('assessment_id')==record['id'] and value.get('origin')==record['origin'] and isinstance(value.get('plan_signature'),str) and hmac.compare_digest(value['plan_signature'],signature(runtime,value))
def ref(source,needle,*,origin='operation_script'):
    offset=source.find(needle)
    return dict(source=origin,line=source.count('\n',0,offset)+1,expression=needle) if offset>=0 else None

def contract(source,metadata):
    """Observed input references, not a claim of complete JavaScript parsing."""
    found={}
    patterns=(('path',r'request\.pathParams(?:\.([A-Za-z_]\w*)|\[[\'\"]([^\'\"]+)[\'\"]\])'),
        ('query',r'request\.queryParams(?:\.([A-Za-z_]\w*)|\[[\'\"]([^\'\"]+)[\'\"]\])'),
        ('body',r'(?:request_body|requestPayload)\s*(?:\.([A-Za-z_]\w*)|\[[\'\"]([^\'\"]+)[\'\"]\])'),
        ('body.params',r'getParamAsString\([\'\"]([^\'\"]+)[\'\"]\)'))
    for location,pattern in patterns:
        for match in re.finditer(pattern,source):
            name=next(x for x in match.groups() if x is not None)
            found[location,name]=dict(location=location,name=name,line=source.count('\n',0,match.start())+1,expression=match.group())
    try:example=json.loads(metadata.get('request_example') or '{}')
    except (TypeError,ValueError):example={}
    if isinstance(example,dict):
        for name,value in example.items():found.setdefault(('body',name),dict(location='body',name=name,source='request_example',example=value))
    calls=[]
    for match in re.finditer(r'(?:new\s+([A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*)\s*\(|\b([A-Za-z_]\w*(?:\.[A-Za-z_]\w*){1,3})\s*\()',source):
        name=next(x for x in match.groups() if x)
        if name not in {x['name'] for x in calls}:calls.append(dict(name=name,line=source.count('\n',0,match.start())+1))
    return dict(inputs=list(found.values()),calls=calls,request_example=example,consumes=metadata.get('consumes',''),produces=metadata.get('produces',''),analysis_limit='Static input references plus reviewed handler-specific control flow')

def analyze(runtime,record,context,observations):
    from .public_rest import candidates,read_rows,REVIEWED
    inventory=runtime.inventory(record);all_rows=candidates(observations);selected=all_rows[:10]
    nonce='artex-'+uuid.uuid4().hex;missing=uuid.uuid4().hex
    fixtures={};helpers={};plans=[];records=[]
    def lookup(label,table,fields,query,max_records=20):
        if label not in fixtures:fixtures[label]=dict(table=table,query=query,**read_rows(inventory,context,table,fields,query,max_records=max_records,page_size=min(20,max_records)))
        return fixtures[label]['records']
    for name in HELPERS:
        value=read_rows(inventory,context,'sys_script_include','sys_id,name,api_name,sys_policy,script','api_name='+name,max_records=2,page_size=2)
        rows=value['records'];row=rows[0] if len(rows)==1 and value['complete'] else {}
        source=row.get('script')
        helpers[name]=dict(sys_id=row.get('sys_id'),source_available=isinstance(source,str),source_sha256=sha(source) if isinstance(source,str) else None,script=source if isinstance(source,str) else '',pages=value['pages'],limitation='' if isinstance(source,str) else 'Native, protected, or inaccessible Script Include')
    users=lookup('current_user','sys_user','sys_id,user_name,active','user_name='+inventory.username,2) if inventory.username else []
    user=users[0]['sys_id'] if users else None
    refs=lookup('reference_questions','item_option_new','sys_id,name,type,reference,cat_item','active=true^reference=sys_user^typeIN8,31')
    targets=lookup('boolean_targets','item_option_new','sys_id,name,type,dynamic_value_dot_walk_path','active=true^dynamic_value_dot_walk_path=active')
    producers=lookup('producers','sc_cat_item_producer','sys_id,name,active','active=true',5)
    items=lookup('catalog_items','sc_cat_item','sys_id,name,active,sys_class_name','active=true^sys_class_name=sc_cat_item',5)
    links=lookup('public_document_links','ds_document_permalink','sys_id,link_id,document,type,access_type,active,expiration','active=true^type=public',5)
    articles=lookup('knowledge_articles','kb_knowledge','sys_id,number,kb_knowledge_base,short_description','workflow_state=published^short_descriptionLIKEdemo',5)
    portals=lookup('knowledge_portals','sp_portal','sys_id,url_suffix','url_suffix=kb',2)
    lookup('missing_capability','sys_one_extend_capability','sys_id','sys_id='+missing,2)
    lookup('missing_user','sys_user','sys_id','sys_id='+missing,2)
    for row in selected:
        context.check()
        live=read_rows(inventory,context,'sys_ws_operation',FIELDS,'sys_id='+row['sys_id'],max_records=2,page_size=2)
        actual=live['records'][0] if live['complete'] and len(live['records'])==1 else {}
        source=actual.get('operation_script','');digest=sha(source) if isinstance(source,str) and source else None
        review=next((p for p in REVIEWED if p['route']==actual.get('operation_uri') and p['method']==actual.get('http_method') and p['source_sha256']==digest),None)
        source=source if isinstance(source,str) else ''
        analysis=contract(source,actual);limitations=[];cases=[]
        query_metadata=read_rows(inventory,context,'sys_ws_query_parameter_map','web_service_query_parameter.name,web_service_query_parameter.required,web_service_query_parameter.example_value,web_service_query_parameter.short_description','web_service_operation='+row['sys_id'],max_records=20,page_size=20)
        analysis['declared_query_parameters']=query_metadata
        dependency_pins=review.get('helper_pins',{}) if review else {}
        helpers_match=all(helpers.get(name,{}).get('source_sha256')==pin for name,pin in dependency_pins.items())
        def add(key,description,params=None,body=None,query=None,*,expected,branch,fixture_names=(),safety='read_only',control=True):
            evidence=ref(source,branch)
            if evidence is None:raise ValueError('Reviewed source branch missing: '+key)
            case=dict(case_id=row['sys_id']+':'+key,label=key,description=description,method=actual['http_method'],path_params=params or {},query=query or {},body=body,source_evidence=evidence,source_sha256=digest,helper_pins=dependency_pins,fixture_refs=list(fixture_names),safety=safety,expected=expected,allow_control=control)
            cases.append(case)
        kind=review.get('kind') if review and helpers_match else None
        if not kind:limitations.append('Handler or required helper source has no matching reviewed contract; no handler invocation')
        if kind=='shipping_address':
            if user:add('existing-user','Existing authenticated account exercises record and requested-for authorization',{'user_id':user},expected={'status':[200],'result':'address'},branch='sn_sc.CartJS.getRequestedForAddress(userID)',fixture_names=('current_user',))
            if fixtures['missing_user']['complete'] and not fixtures['missing_user']['records']:add('missing-user','Control for invalid-user branch',{'user_id':missing},expected={'status':[400],'text':'Invalid User ID'},branch='Invalid User ID!',fixture_names=('missing_user',))
        elif kind=='dynamic_value':
            trigger=next((x for x in refs if x.get('type')=='8'),refs[0] if refs else None);target=targets[0] if targets else None
            if user and trigger and target:
                base={'sysparm_dynamic_record_id':user,'sysparm_dynamic_target_fields':target['sys_id']}
                add('existing-reference','Read the existing account active flag through actual reference and target questions',{'sys_id':trigger['sys_id']},base,expected={'status':[200],'result':'dynamic_value','field':target['name']},branch='if (isPresent && gr.canRead())',fixture_names=('current_user','reference_questions','boolean_targets'))
                add('missing-record','Compare record authorization for a confirmed nonexistent user',{'sys_id':trigger['sys_id']},dict(base,sysparm_dynamic_record_id=missing),expected={'status':[200],'result':'empty_dynamic_value','field':target['name']},branch='Record does not exist or record access issue',fixture_names=('missing_user','reference_questions','boolean_targets'))
                add('mixed-target-list','Exercise comma-separated target selection and unknown-question handling',{'sys_id':trigger['sys_id']},dict(base,sysparm_dynamic_target_fields=target['sys_id']+','+missing),expected={'status':[200],'result':'dynamic_value','field':target['name']},branch="targetFields.split(',')",fixture_names=('current_user','reference_questions','boolean_targets'))
                add('wrong-question-type','Use an existing non-reference question to reach the type guard',{'sys_id':target['sys_id']},base,expected={'status':[400],'text':'not of type reference/requested for'},branch='Trigger question not of type reference/requested for',fixture_names=('reference_questions','boolean_targets'))
            else:limitations.append('No readable reference-question, boolean target, or user fixture')
        elif kind=='knowledge_facets':
            article=articles[0] if articles else {};keyword=article.get('number',nonce)
            body={'params':{'keyword':keyword,'language':'en','variables':{},'query':'','order':'relevancy','paginationMinCount':'1','portal_suffix':'kb' if portals else ''}}
            add('valid-search','Full parameter object using a published demo article',body=body,expected={'status':[200],'result':'facets'},branch='kbService.getAllFacets(',fixture_names=('knowledge_articles','knowledge_portals'))
            alternate=json.loads(json.dumps(body));alternate['params']['keyword']=nonce;alternate['params']['query']={};alternate['params']['paginationMinCount']=1
            add('object-coercion','Exercise object-to-JSON and number-to-string parameter coercion',body=alternate,expected={'status':[200],'result':'facets'},branch="typeof val === 'object'",fixture_names=('knowledge_portals',))
            add('missing-params','Negative control for the required params envelope',body={},expected={'status':[400],'text':'No input data provided'},branch='No input data provided')
        elif kind in ('document_info','document_download'):
            branch='getPermalinkInfo(linkId)' if kind=='document_info' else 'getPermalinkInputStream(linkId)'
            add('missing-link','Control for a nonexistent link',{'linkId':nonce},expected={'status':[200] if kind=='document_info' else [404],'text':'Permalink not found'},branch=branch)
            link=next((x for x in links if x.get('link_id')),None)
            if link:add('existing-public-link','Resolve an existing active public document link',{'linkId':link['link_id']},expected={'status':[200],'result':'permalink'},branch=branch,fixture_names=('public_document_links',))
            else:limitations.append('No existing active public document permalink; successful-document path untested')
        elif kind=='record_producer':
            if producers:
                producer=producers[0];lookup('producer_variables','item_option_new','sys_id,name,type,mandatory,reference','active=true^cat_item='+producer['sys_id'])
                add('existing-producer-rejection','Valid producer, explicit mobile query and body, rejected before submitProducer',{'sys_id':producer['sys_id']},{'variables':{},'sysparm_no_validation':'false','sysparm_also_request_for':user or nonce},{'sysparm_view':'mobile'},expected={'status':[400],'text':'Cannot request Record Producer for other users'},branch='Cannot request Record Producer for other users.',fixture_names=('producers','producer_variables'),safety='pre_effect_rejection')
                limitations.append('Successful submitProducer would create a record and is outside this read-only assessment')
            else:limitations.append('No readable record-producer fixture')
        elif kind=='order_now':
            if items:
                item=items[0];lookup('catalog_variables','item_option_new','sys_id,name,type,mandatory,reference','active=true^cat_item='+item['sys_id'])
                add('existing-item-invalid-quantity','Existing catalog item and complete envelope; reject before CatItem/cart construction',{'sys_id':item['sys_id']},{'sysparm_quantity':'-1','sysparm_no_validation':'false','variables':{}},expected={'status':[400],'text':'Invalid Quantity value'},branch='Invalid Quantity value',fixture_names=('catalog_items','catalog_variables'),safety='pre_effect_rejection')
                limitations.append('A positive quantity can reach cart.orderNow; successful ordering is outside read-only scope')
            else:limitations.append('No readable catalog-item fixture')
        elif kind=='reseed':
            if fixtures['missing_capability']['complete'] and not fixtures['missing_capability']['records']:
                encode=lambda value:base64.urlsafe_b64encode(json.dumps(value,separators=(',',':')).encode()).decode().rstrip('=')
                jwt=encode({'alg':'none','typ':'JWT'})+'.'+encode({'sub':nonce,'exp':1})+'.'
                add('invalid-jwt','Provide the named JWT property and DELTA branch selector; nonexistent capability prevents reseed effects',{'sys_id':missing},{'Glide-JWT':jwt,'reseedType':'DELTA'},expected={'status':[401],'text':'Invalid JWT'},branch='Authorization failed - Invalid JWT',fixture_names=('missing_capability',),safety='pre_effect_rejection')
                add('missing-jwt','Separate missing-token gate from signature validation',{'sys_id':missing},{'reseedType':'DELTA'},expected={'status':[400],'text':'Missing JWT'},branch='Missing JWT',fixture_names=('missing_capability',),safety='pre_effect_rejection')
            limitations.append('Valid-token reseeding would mutate instance state; not executed')
        elif kind=='validate_token':
            add('invalid-token','Exercise membership comparison and the invalid-token logging branch',{'token':nonce},expected={'status':[200],'text':'false'},branch='value.split(",").indexOf(validationToken)')
            limitations.append('Configured validation tokens are not retrieved or placed in test requests')
        if not source and actual.get('request_example'):limitations.append('Request example describes the input, but protected handler behavior and signing effects remain unverified')
        records.append(dict(operation_id=row['sys_id'],name=row['name'],method=row['http_method'],route=row['operation_uri'],source_sha256=digest,source_available=bool(source),source=source,contract=analysis,helper_pins=dependency_pins,cases=cases,limitations=limitations,metadata_pages=live['pages']))
        plans.extend(cases)
    result=dict(resource='public_rest_source',required_capability='administrator',status=200,assessment_id=record['id'],origin=record['origin'],record_count=len(records),records=records,helpers=helpers,fixtures=fixtures,cases=plans,selected_ids=[r['sys_id'] for r in selected],all_operation_contracts=[dict(operation_id=r['sys_id'],name=r['name'],**contract(r.get('operation_script',''),r)) for r in all_rows],limitations=['Native/protected helper implementations remain explicit call-graph boundaries','Effectful success paths are not executed by this read-only definition'])
    result['plan_signature']=signature(runtime,result)
    return result
