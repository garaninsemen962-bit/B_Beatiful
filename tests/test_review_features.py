"""Exact pivot, frozen rechecks and Gemini evidence regressions."""
import hashlib,io,json,os,shutil,subprocess,sys,time,unittest,urllib.parse,zipfile
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import test_http as helpers
import store as s,service,sandbox,reports,pivot,ai_review,lifecycle,gemini_client
class ReviewTests(unittest.TestCase):
 setUp=helpers.HttpTests.setUp
 tearDown=helpers.HttpTests.tearDown
 client=helpers.HttpTests.client
 request=helpers.HttpTests.request
 csrf=helpers.HttpTests.csrf
 approve=helpers.HttpTests.approve
 def fixture(self):
  raw=io.BytesIO()
  with zipfile.ZipFile(raw,'w') as z:
   for source,docs in [('BEFORE',{'A1':{'a':None,'gone':1,'flag':False},'A2':{'a':1}}),('AFTER',{'A1':{'a':False,'new':None,'flag':0},'A2':{'a':2}})]:
    for app,doc in docs.items():z.writestr('RESULTS/'+source+'/'+app+'.json',json.dumps(doc))
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
  status,body,_=self.request(self.a,link);self.assertEqual(status,200,body);self.assertIn('2 заявок',body)
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
  proc=subprocess.run([sys.executable,str(s.ROOT/'engine.py'),str(result)],capture_output=True,timeout=20);self.assertEqual(proc.returncode,0,proc.stderr.decode());self.assertFalse((result/'mart.sqlite').exists());self.assertEqual(json.loads((result/'checks.json').read_text())['rows'][0]['METRIC'],1)
  self.assertEqual(hashlib.sha256(parent.read_bytes()).hexdigest(),digest);self.assertEqual(hashlib.sha256((folder/'input/mart.sqlite').read_bytes()).hexdigest(),digest)
 def test_gemini_validation_async_cache_and_escaping(self):
  eid,rid,_,_=self.fixture();refs,payload=ai_review.evidence(eid);self.assertNotIn('APP_IDS',json.dumps(payload))
  result={'summary':'<script>не исполнять</script>','findings':[{'severity':'high','observation':'Изменения','hypothesis':'Нужна проверка','recommendation':'Сверить','evidence_ids':['E1']}],'limitations':['Только агрегаты']}
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
if __name__=='__main__':unittest.main()
