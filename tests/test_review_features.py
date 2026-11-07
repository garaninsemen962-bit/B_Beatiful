"""Exact pivot, frozen rechecks and Gemini evidence regressions."""
import hashlib,io,json,os,shutil,subprocess,sys,time,unittest,urllib.parse,zipfile
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import test_http as helpers
import store as s,service,sandbox,reports,pivot,ai_review,lifecycle,gemini_client
def detailed_response(summary='Подробное резюме: несколько изменений требуют проверки.'):
 return {'summary':summary,'assessment':'Нужно вручную проверить наиболее затронутые заявки.','findings':[{'title':'Изменения атрибутов','severity':'medium','observation':'Есть различия','quantitative_details':'Две заявки имеют изменения; это не сумма строк витрины.','interpretation':'Различия требуют проверки правил сопоставления.','hypothesis':'Возможна смена правила','alternatives':['Возможна ошибка нормализации'],'recommendation':'Открыть выбранную заявку','evidence_ids':['E1']}],'review_plan':[{'priority':'P1','action':'Открыть изменившиеся заявки и проверить исходные значения.','expected_result':'Установить, отражает ли различие изменение данных или сопоставления.','evidence_ids':['E1']}],'limitations':['Тест использует только агрегаты.']}

class ReviewTests(unittest.TestCase):
 setUp=helpers.HttpTests.setUp
 tearDown=helpers.HttpTests.tearDown
 client=helpers.HttpTests.client
 request=helpers.HttpTests.request
 csrf=helpers.HttpTests.csrf
 approve=helpers.HttpTests.approve
 def fixture(self,docs=None):
  raw=io.BytesIO()
  with zipfile.ZipFile(raw,'w') as z:
   for source,source_docs in (docs or dict([('BEFORE',{'A1':{'a':None,'gone':1,'flag':False},'A2':{'a':1}}),('AFTER',{'A1':{'a':False,'new':None,'flag':0},'A2':{'a':2}})])).items():
    for app,doc in source_docs.items():z.writestr('RESULTS/'+source+'/'+app+'.json',doc if isinstance(doc,bytes) else json.dumps(doc))
  dataset=service.save_dataset(self.admin,{'name':'Малый датасет'},raw.getvalue());ids={}
  for name,kind in [('normalize','normalizer'),('compare','comparator'),('aggregate','aggregator')]:ids[kind]=service.save_script(self.admin,{'name':name,'script_kind':kind},(s.ROOT/'procedures'/str(name+'.py')).read_text())
  code='''"""Число различий\n\nПроверка для повторного запуска."""
def run(mart, params=None):
    return [dict(LABEL="Изменения",SOURCE="AFTER",ARGUMENT="",CNT=mart.count(diff_flag=1),TOTAL=mart.count(),METRIC=1,APP_IDS=mart.app_ids(diff_flag=1),RESULT="FAIL",CONFIDENCE="HIGH")]
CHECK={"entrypoint":run,"params":{}}
'''
  check=service.save_script(self.admin,{'name':'Проверка','script_kind':'check'},code)
  cfg=service.save_config(self.admin,{'name':'Конфигурация','dataset':dataset,**ids,'checks':[check]});rid=service.create_run(self.admin,{'name':'Прогон','config':cfg})
  with patch.object(sandbox,'available',return_value=(True,'')),patch.object(sandbox,'launch'):eid=service.execute(self.admin,rid)
  folder=s.STATE/'executions'/eid;result=folder/'artifacts';result.mkdir();shutil.copytree(folder/'input/scripts',result/'scripts');config=json.loads((folder/'input/config.json').read_text())
  with s.db() as c:config['dataset']=str(s.STATE/'objects'/json.loads(s.resource(c,dataset)['body'])['folder'])
  (result/'config.json').write_text(json.dumps(config))
  # Trusted bundled procedures only: this is not a production host execution path.
  proc=subprocess.run([sys.executable,str(s.ROOT/'engine.py'),str(result)],capture_output=True,timeout=20);self.assertEqual(proc.returncode,0,proc.stderr.decode());sandbox.finish(eid,'done');reports.facts.cache_clear()
  return eid,rid,check,code
 def test_exact_pivot_drill_layout_and_raw_types(self):
  eid,rid,check,_=self.fixture();base='/executions/'+eid
  selected=list(reports.selected(eid,{'filter':json.dumps({'SOURCE':'AFTER'})}));self.assertEqual(len(set().union(*(apps for _,apps in selected))),2);self.assertGreater(sum(len(apps) for _,apps in selected),2)
  status,body,_=self.request(self.a,base+'/pivot');self.assertEqual(status,200,body);self.assertIn('data-pivot-layout',body);self.assertIn('WebDataRocks',body)
  from html import unescape
  import re
  links=[unescape(x) for x in re.findall('href="([^"]+)"',body) if '/applications?' in x]
  def filters(x):return json.loads(urllib.parse.parse_qs(urllib.parse.urlparse(x).query)['filter'][0])
  link=next(x for x in links if filters(x).get('PATH')=='a' and filters(x).get('SOURCE')=='AFTER')
  status,body,_=self.request(self.a,link);self.assertEqual(status,200,body);self.assertIn('2 заявки',body)
  card=next(unescape(x) for x in re.findall('href="([^"]+)"',body) if '/applications/A1?' in x)
  status,body,_=self.request(self.a,card);self.assertEqual(status,200,body);self.assertIn('id="focus"',body);self.assertIn('>null</pre>',body);self.assertIn('>false</pre>',body);self.assertIn('SOURCE',card)
  csrf=self.csrf(self.a);layout={'group':'CHANGE_TYPE','columns':'SOURCE','diff':'all'}
  self.assertEqual(self.request(self.a,base+'/layout',{'csrf':csrf,'layout':json.dumps(layout)})[0],200);self.assertEqual(pivot.load(self.admin,eid)['group'],'CHANGE_TYPE')
  self.approve()
  with s.db() as c:c.execute("UPDATE resources SET visibility='public' WHERE id=?",(rid,))
  token=self.csrf(self.b)
  self.assertEqual(self.request(self.b,base+'/recheck',{'csrf':token,'checks':[check]})[0],403);self.assertEqual(self.request(self.b,base+'/ai',{'csrf':token})[0],403)
  self.assertEqual(self.request(self.b,base+'/layout',{'csrf':token,'layout':json.dumps(layout)})[0],200)
  self.assertEqual(self.request(self.a,base+'/layout',{'csrf':csrf,'layout':'{}','kind':'wdr'})[0],200)
  with patch.dict(os.environ,{'DD_PUBLIC_URL':'https://analysis.example.test:8443'}):
   status,body,_=self.request(self.a,base+'/export');self.assertEqual(status,200,body);self.assertIn('https://analysis.example.test:8443/executions/',body)
 def test_recheck_snapshots_and_no_normalizer(self):
  eid,rid,check,code=self.fixture();parent=s.STATE/'executions'/eid/'artifacts/mart.sqlite';digest=hashlib.sha256(parent.read_bytes()).hexdigest()
  with patch.object(sandbox,'available',return_value=(True,'')),patch.object(sandbox,'launch') as launch:
   child=service.recheck(self.admin,eid,{'checks':[check],'params':'{}'});launch.assert_called_once_with(child)
  folder=s.STATE/'executions'/child;cfg=json.loads((folder/'input/config.json').read_text());self.assertEqual(cfg['mode'],'checks');self.assertEqual(hashlib.sha256((folder/'input/mart.sqlite').read_bytes()).hexdigest(),digest)
  cmd=sandbox.command(child,folder,None,'pinned-image',folder/'input/mart.sqlite');self.assertIn('--network=none',cmd);self.assertTrue(any('target=/source/mart.sqlite,readonly' in x for x in cmd));self.assertFalse(any('target=/dataset' in x for x in cmd))
  service.save_script(self.admin,{'name':'Новая версия','script_kind':'check','revision':'1'},code.replace('METRIC=1','METRIC=999'),check)
  result=folder/'test-result';result.mkdir();shutil.copytree(folder/'input/scripts',result/'scripts');cfg['source_mart']=str(folder/'input/mart.sqlite');(result/'config.json').write_text(json.dumps(cfg))
  for key in ('normalizer','comparator','aggregator'):self.assertNotIn(key,cfg)
  proc=subprocess.run([sys.executable,str(s.ROOT/'engine.py'),str(result)],capture_output=True,timeout=20);self.assertEqual(proc.returncode,0,proc.stderr.decode());self.assertFalse((result/'mart.sqlite').exists());self.assertEqual(next(x for x in json.loads((result/'checks.json').read_text())['rows'] if x['CHECK_FILE']!='__input_quality__')['METRIC'],1)
  self.assertEqual(hashlib.sha256(parent.read_bytes()).hexdigest(),digest);self.assertEqual(hashlib.sha256((folder/'input/mart.sqlite').read_bytes()).hexdigest(),digest)
 def test_gemini_validation_async_cache_and_escaping(self):
  eid,rid,_,_=self.fixture();refs,payload=ai_review.evidence(eid);self.assertNotIn('APP_IDS',json.dumps(payload))
  result=detailed_response('<script>не исполнять</script>')
  bad=json.loads(json.dumps(result));bad['findings'][0]['evidence_ids']=['E999']
  with self.assertRaises(ValueError):ai_review.validate(bad,{x['id'] for x in refs})
  with patch.dict(os.environ,{'GEMINI_API_KEY':'unit-test-secret','GEMINI_MODEL':'test-model'}),patch.object(ai_review,'transport',return_value=result) as transport:
   aid=ai_review.start(self.admin,eid)
   for _ in range(100):
    with s.db() as c:row=dict(c.execute('SELECT * FROM analyses WHERE id=?',(aid,)).fetchone())
    if row['state'] in ('done','failed'):break
    time.sleep(.01)
   self.assertEqual(row['state'],'done',row['error']);self.assertEqual(ai_review.start(self.admin,eid),aid);self.assertEqual(transport.call_count,1)
   status,body,_=self.request(self.a,'/executions/'+eid+'/ai');self.assertEqual(status,200,body);self.assertIn('&lt;script&gt;',body);self.assertNotIn('unit-test-secret',body);self.assertIn('Основания',body)
  with s.db() as c:c.execute("UPDATE analyses SET state='running' WHERE id=?",(aid,))
  with self.assertRaises(ValueError):lifecycle.execution_action(self.admin,eid,'delete')
  ai_review.recover()
  with s.db() as c:self.assertEqual(c.execute('SELECT state FROM analyses WHERE id=?',(aid,)).fetchone()[0],'failed')
 def test_gemini_transport_key_and_http_errors(self):
  response={'candidates':[{'finishReason':'STOP','content':{'parts':[{'text':'{"summary":"ok","findings":[],"limitations":[]}' }]}}]}
  class Reply:
   def __enter__(self):return self
   def __exit__(self,*args):pass
   def read(self,n):return json.dumps(response).encode()
  with patch.dict(os.environ,{'GEMINI_API_KEY':'super-secret'}),patch.object(gemini_client.urllib.request,'urlopen',return_value=Reply()) as send:
   self.assertEqual(ai_review.transport('test-model',[])['summary'],'ok');request=send.call_args.args[0];self.assertNotIn('super-secret',request.full_url);self.assertNotIn('super-secret',request.data.decode());self.assertEqual(request.get_header('X-goog-api-key'),'super-secret')
  import urllib.error
  with patch.dict(os.environ,{'GEMINI_API_KEY':'fake-key'}),patch.object(gemini_client.urllib.request,'urlopen',side_effect=urllib.error.HTTPError('url?key=secret',429,'secret',{},None)):
   with self.assertRaisesRegex(ValueError,'квота'):ai_review.transport('test-model',[])

class InteractiveAITests(ReviewTests):
 # Do not inherit the full test suite; explicit load_tests below selects only the new methods.
 def test_async_api_selected_model_history_acl_and_legacy(self):
  import threading
  eid,rid,_,_=self.fixture();api='/api/executions/'+eid+'/ai';base='/executions/'+eid+'/ai';csrf=self.csrf(self.a);gate=threading.Event();calls=[]
  def fake(model,payload,system=None):calls.append(model);gate.wait(5);return detailed_response()
  with patch.dict(os.environ,{'GEMINI_API_KEY':'fake-api-key','GEMINI_MODEL':'gemini-3.5-flash-lite'}),patch.object(ai_review,'transport',side_effect=fake):
   status,body,_=self.request(self.a,base);self.assertEqual(status,200);self.assertNotIn('http-equiv="refresh"',body);self.assertIn('/assets/ai.js',body);self.assertIn('name="model"',body)
   self.assertEqual(self.request(self.a,api,{'model':'gemini-3.8-flash'})[0],403)
   status,body,_=self.request(self.a,api,{'csrf':csrf,'model':'gemini-3.8-flash'});self.assertEqual(status,202,body);data=json.loads(body);self.assertIn(data['state'],('queued','running'));aid=data['analysis'];self.assertIn('Анализ выполняется',data['html'])
   self.assertEqual(json.loads(self.request(self.a,api+'?analysis='+aid)[1])['analysis'],aid)
   gate.set()
   for _ in range(100):
    data=json.loads(self.request(self.a,api+'?analysis='+aid)[1])
    if data['state'] in ('done','failed'):break
    time.sleep(.01)
   self.assertEqual(data['state'],'done',data['html']);self.assertEqual(calls,['gemini-3.8-flash']);self.assertIn('Масштаб и цифры',data['html']);self.assertIn('Порядок проверки',data['html']);self.assertEqual(data['html'].count('Модель gemini-3.8-flash'),1)
  with s.db() as c:
   # Existing v1 result remains readable, unchanged, without a new model call.
   old=s.uid();legacy={'summary':'Старое резюме','findings':[{'severity':'low','observation':'Факт','hypothesis':'Гипотеза','recommendation':'Проверка','evidence_ids':['E1']}],'limitations':['Ограничение']}
   c.execute('INSERT INTO analyses(id,execution,actor,state,model,fingerprint,evidence,result,error,created,finished) VALUES (?,?,?,?,?,?,?,?,?,?,?)',(old,eid,self.admin['id'],'done','old-model','legacy',json.dumps([{'id':'E1','kind':'summary','data':{},'url':'/executions/'+eid}]),json.dumps(legacy),'',1,2))
  self.assertIn('Старое резюме',json.loads(self.request(self.a,api+'?analysis='+old)[1])['html'])
  uid=self.approve()
  with s.db() as c:c.execute("UPDATE resources SET visibility='public' WHERE id=?",(rid,))
  self.assertEqual(self.request(self.b,api+'?analysis='+aid)[0],200)
  token=self.csrf(self.b);self.assertEqual(self.request(self.b,api,{'csrf':token,'model':'gemini-3.8-flash'})[0],403);self.assertEqual(self.request(self.b,api+'/models')[0],403)
  self.assertEqual(self.request(self.a,api+'?analysis=not-this-analysis')[0],404)
  with s.db() as c:c.execute("UPDATE resources SET visibility='private' WHERE id=?",(rid,))
  self.assertEqual(self.request(self.b,api+'?analysis='+aid)[0],403)
 def test_evidence_intersections_and_new_schema_requirements(self):
  eid,_,_,_=self.fixture();import sqlite3
  path=s.STATE/'executions'/eid/'artifacts/mart.sqlite'
  with sqlite3.connect(path) as c:
   for row in c.execute('SELECT APP_ID,data FROM segments').fetchall():
    seg=json.loads(row[1]);seg['PRODUCTS']='P1;P2' if row[0]=='A1' else 'P1';seg['DECISION_CHANGED']=int(row[0]=='A1');seg['DECISION_BEFORE']='REJECT';seg['DECISION_AFTER']='APPROVE' if row[0]=='A1' else 'REJECT';c.execute('UPDATE segments SET data=? WHERE APP_ID=?',(json.dumps(seg),row[0]))
  c.execute('PRAGMA wal_checkpoint(TRUNCATE)');c.close()
  refs,payload=ai_review.evidence(eid);self.assertNotIn('APP_IDS',json.dumps(payload));self.assertNotIn('A1',json.dumps(payload));self.assertNotIn('A2',json.dumps(payload))
  products={x['data']['product']:x['data'] for x in refs if x['kind']=='product'};self.assertEqual(products['P1']['unique_apps'],2);self.assertEqual(products['P1']['decision_changed'],1);self.assertEqual(products['P2']['unique_apps'],1)
  changed=next(x['data'] for x in refs if x['kind']=='changed_path' and x['data']['PATH']=='a');self.assertEqual(changed['unique_apps'],2);self.assertEqual(changed['decision_changed_among_affected'],1)
  result=detailed_response();del result['findings'][0]['quantitative_details']
  with self.assertRaisesRegex(ValueError,'quantitative_details'):ai_review.validate(result,{'E1'})
  result=detailed_response();result['review_plan'][0]['evidence_ids']=['E999']
  with self.assertRaisesRegex(ValueError,'доказательство'):ai_review.validate(result,{'E1'})
 def test_models_catalog_filtering_cache_and_api(self):
  import ai_models
  ai_models._cache.clear()
  pages=[{'models':[{'name':'models/gemini-3.8-flash','displayName':'Flash','supportedGenerationMethods':['generateContent']},{'name':'models/gemini-3.8-flash-tts','supportedGenerationMethods':['generateContent']},{'name':'models/gemini-embedding-001','supportedGenerationMethods':['embedContent']}],'nextPageToken':'next'},{'models':[{'name':'models/gemini-3.5-flash-lite','supportedGenerationMethods':['generateContent']}]}]
  class Reply:
   def __init__(self,value):self.value=value
   def __enter__(self):return self
   def __exit__(self,*args):pass
   def read(self,n):return json.dumps(self.value).encode()
  with patch.dict(os.environ,{'GEMINI_API_KEY':'catalog-unit-key'}),patch.object(ai_models.urllib.request,'urlopen',side_effect=[Reply(x) for x in pages]) as send:
   models=ai_models.catalog();self.assertEqual({x['id'] for x in models},{'gemini-3.8-flash','gemini-3.5-flash-lite'});self.assertEqual(ai_models.catalog(),models);self.assertEqual(send.call_count,2)
   for call in send.call_args_list:self.assertNotIn('catalog-unit-key',call.args[0].full_url)
  eid,_,_,_=self.fixture()
  with patch.object(ai_models,'catalog',return_value=models):
   status,body,_=self.request(self.a,'/api/executions/'+eid+'/ai/models');self.assertEqual(status,200,body);self.assertEqual(len(json.loads(body)['models']),2)
  with patch.dict(os.environ,{'GEMINI_MODELS':'gemini-3.5-flash-lite'}),self.assertRaisesRegex(ValueError,'не разрешена'):ai_models.validate('gemini-3.8-flash')

def load_tests(loader,tests,pattern):
 suite=unittest.TestSuite();suite.addTests(loader.loadTestsFromTestCase(ReviewTests))
 for name in ('test_async_api_selected_model_history_acl_and_legacy','test_evidence_intersections_and_new_schema_requirements','test_models_catalog_filtering_cache_and_api'):suite.addTest(InteractiveAITests(name))
 return suite

if __name__=='__main__':unittest.main()
