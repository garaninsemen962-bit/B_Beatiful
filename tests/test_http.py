import io,json,re,sys,tempfile,threading,unittest,urllib.request,urllib.parse,urllib.error,http.cookiejar,zipfile
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import app,store as s,sandbox,reports
class HttpTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.old=s.STATE;s.STATE=Path(self.tmp.name);sandbox.STATE=s.STATE;reports.STATE=s.STATE;s.init()
  self.admin=s.register('admin','Администратор','test-password',True)
  self.server=app.ThreadingHTTPServer(('127.0.0.1',0),app.Handler);self.base='http://127.0.0.1:'+str(self.server.server_port)
  self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
  self.a=self.client();self.b=self.client();self.request(self.a,'/login',{'login':'admin','password':'test-password'})
 def tearDown(self):
  self.server.shutdown();self.server.server_close();self.thread.join();s.STATE=self.old;sandbox.STATE=self.old;reports.STATE=self.old;self.tmp.cleanup()
 def client(self):return urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
 def request(self,client,path,fields=None,files=None):
  headers={};data=None
  if files:
   boundary='TESTBOUNDARY';chunks=[]
   for key,value in fields.items():chunks.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"\r\n\r\n{value}\r\n'.encode())
   for key,(filename,content) in files.items():chunks.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"; filename="{filename}"\r\nContent-Type: application/octet-stream\r\n\r\n'.encode()+content+b'\r\n')
   data=b''.join(chunks)+f'--{boundary}--\r\n'.encode();headers['Content-Type']='multipart/form-data; boundary='+boundary
  elif fields is not None:data=urllib.parse.urlencode(fields,doseq=True).encode()
  try:
   with client.open(urllib.request.Request(self.base+path,data=data,headers=headers)) as r:return r.status,r.read().decode(),r.url
  except urllib.error.HTTPError as e:return e.code,e.read().decode(),e.url
 def csrf(self,client,path='/runs'):
  status,body,_=self.request(client,path);self.assertEqual(status,200)
  match=re.search(r'name="csrf"[^>]*value="([^"]+)"',body);self.assertIsNotNone(match);return match[1]
 def approve(self):
  self.request(self.b,'/register',{'login':'member','display':'Иван','password':'test-password'})
  with s.db() as c:uid=c.execute("SELECT id FROM users WHERE login='member'").fetchone()[0]
  token=self.csrf(self.a,'/admin');self.request(self.a,'/admin/users/'+uid,{'csrf':token,'status':'active'})
  self.request(self.b,'/login',{'login':'member','password':'test-password'});return uid
 def test_pending_approval_block_and_csrf(self):
  code,body,url=self.request(self.b,'/register',{'login':'member','display':'Иван','password':'test-password'});self.assertIn('/account',url)
  _,_,url=self.request(self.b,'/datasets');self.assertIn('/account',url)
  uid=None
  with s.db() as c:uid=c.execute("SELECT id FROM users WHERE login='member'").fetchone()[0]
  self.assertEqual(self.request(self.b,'/admin')[1].count('Пользователи'),0)
  token=self.csrf(self.a,'/admin');self.request(self.a,'/admin/users/'+uid,{'csrf':token,'status':'active'})
  self.request(self.b,'/login',{'login':'member','password':'test-password'});self.assertIn('Прогоны',self.request(self.b,'/runs')[1])
  self.assertEqual(self.request(self.b,'/runs/new',{'name':'evil','csrf':'bad'})[0],403)
  self.request(self.a,'/admin/users/'+uid,{'csrf':token,'status':'blocked'});self.assertIn('/login',self.request(self.b,'/runs')[2])
 def test_upload_share_revoke_and_docstring(self):
  uid=self.approve();csrf=self.csrf(self.a)
  code='"""Русская метрика\n\nОписание при выборе."""\nraise RuntimeError("must not execute")\ndef run(mart, params): return []\n'
  status,body,url=self.request(self.a,'/scripts/new',{'csrf':csrf,'name':'','description':'','script_kind':'check','code':''},{'file':('metric.py',code.encode())});self.assertEqual(status,200,body);self.assertIn('Русская метрика',body);rid=url.split('/')[-1].split('?')[0]
  self.assertEqual(self.request(self.b,'/scripts/'+rid)[0],403)
  _,body,_=self.request(self.a,'/resources/'+rid+'/access');stamp=re.search(r'name="stamp"[^>]*value="([^"]+)"',body)[1]
  status,body,_=self.request(self.a,'/resources/'+rid+'/access',{'csrf':csrf,'stamp':stamp,'visibility':'named','public_role':'reader','g_'+uid:'reader'});self.assertEqual(status,200,body)
  self.assertEqual(self.request(self.b,'/scripts/'+rid)[0],200);self.assertEqual(self.request(self.b,'/resources/'+rid+'/download')[0],403)
  self.assertEqual(self.request(self.b,'/scripts/'+rid,{'csrf':self.csrf(self.b),'name':'hack','revision':'1','script_kind':'check','code':code})[0],403)
  _,body,_=self.request(self.a,'/resources/'+rid+'/access');stamp=re.search(r'name="stamp"[^>]*value="([^"]+)"',body)[1]
  self.request(self.a,'/resources/'+rid+'/access',{'csrf':csrf,'stamp':stamp,'visibility':'private','public_role':'reader'})
  self.assertEqual(self.request(self.b,'/scripts/'+rid)[0],403)
  data=io.BytesIO()
  with zipfile.ZipFile(data,'w') as z:
   z.writestr('RESULTS/BEFORE_MASTER/I1.json','{"a":1}');z.writestr('RESULTS/AFTER_BETA/I1.json','{"a":2}')
  status,body,url=self.request(self.a,'/datasets/new',{'csrf':csrf,'name':'Мой датасет','description':'Тест'},{'file':('data.zip',data.getvalue())});self.assertEqual(status,200,body);self.assertIn('Мой датасет',body)
  self.assertEqual(self.request(self.b,urllib.parse.urlparse(url).path)[0],403)
 def test_pages_and_real_navigation(self):
  service=__import__('service');service.import_demo(self.admin)
  for path in ['/runs','/datasets','/scripts','/configurations','/runs/new','/configurations/new','/scripts/new','/datasets/new','/admin']:
   code,body,_=self.request(self.a,path);self.assertEqual(code,200,(path,body));self.assertNotIn('location.hash',body);self.assertIn('<html',body)
 def test_script_errors_keep_code_and_metadata(self):
  from html.parser import HTMLParser
  class Fields(HTMLParser):
   def __init__(self,html):
    super().__init__(convert_charrefs=True);self.values={};self.current=None;self.feed(html)
   def handle_starttag(self,tag,attrs):
    a=dict(attrs)
    if tag=='textarea':self.current=a.get('name');self.values[self.current]=''
    if tag=='input' and 'name' in a:self.values[a['name']]=a.get('value','')
   def handle_data(self,data):
    if self.current:self.values[self.current]+=data
   def handle_endtag(self,tag):
    if tag=='textarea':self.current=None
  csrf=self.csrf(self.a);source='print("Код <тест> & данные")\n'
  fields={'csrf':csrf,'name':'Моя метрика','description':'Описание <не терять>','script_kind':'check','revision':'0','code':source,'draft_token':'draft123'}
  status,body,url=self.request(self.a,'/scripts/new',fields);self.assertEqual(status,422);self.assertIn('run',body);self.assertIn('data-script-error',body)
  parsed=Fields(body).values
  for key in ['code','name','description','revision','draft_token']:self.assertEqual(parsed[key],fields[key])
  self.assertIn('editor.js',body);self.assertIn('data-script-editor',body)
  fields['code']='def run(:\n    pass\n';status,body,_=self.request(self.a,'/scripts/new',fields)
  self.assertEqual(status,422);self.assertEqual(Fields(body).values['code'],fields['code']);self.assertIn('Ошибка синтаксиса',body)
  # Metadata extraction must not require an executable check contract.
  status,body,_=self.request(self.a,'/api/script-info',{'csrf':csrf,'code':'"""Название\n\nОписание."""\nprint(123)','kind':'check'})
  self.assertEqual(status,200,body);self.assertEqual(json.loads(body)['name'],'Название')
 def test_edit_conflict_keeps_unsaved_buffer(self):
  csrf=self.csrf(self.a)
  fields={'csrf':csrf,'name':'Метрика','description':'','script_kind':'check','code':'def run(mart, params=None):\n    return []\n','draft_token':'one'}
  status,body,url=self.request(self.a,'/scripts/new',fields);self.assertEqual(status,200,body)
  rid=urllib.parse.urlparse(url).path.rsplit('/',1)[1]
  fields.update(revision='1',code='def run(mart, params=None):\n    # second version\n    return []\n')
  self.assertEqual(self.request(self.a,'/scripts/'+rid,fields)[0],200)
  before=len(list((s.STATE/'objects').glob('*.py')))
  fields['code']='def run(mart, params=None):\n    # unsaved conflict: ВАЖНО\n    return []\n'
  status,body,_=self.request(self.a,'/scripts/'+rid,fields);self.assertEqual(status,422);self.assertIn('ВАЖНО',body);self.assertIn('уже изменён',body)
  with s.db() as c:self.assertEqual(s.resource(c,rid)['revision'],2)
  self.assertEqual(len(list((s.STATE/'objects').glob('*.py'))),before)
 def test_profile_admin_edit_and_session_revocation(self):
  uid=self.approve();csrf=self.csrf(self.b,'/profile')
  with s.db() as c:revision=c.execute('SELECT revision FROM users WHERE id=?',(uid,)).fetchone()[0]
  status,body,_=self.request(self.b,'/profile',{'csrf':csrf,'revision':revision,'display':'Обновлённое имя','login':'member','admin':'1','status':'blocked'})
  self.assertEqual(status,200,body);self.assertIn('Обновлённое имя',body)
  self.assertEqual(self.request(self.b,'/admin/users/'+self.admin['id']+'/edit')[0],403)
  with s.db() as c:
   target=dict(c.execute('SELECT * FROM users WHERE id=?',(uid,)).fetchone());self.assertEqual(target['admin'],0);self.assertEqual(target['status'],'active')
  status,body,_=self.request(self.b,'/profile',{'csrf':csrf,'revision':target['revision'],'login':'changed','display':'Обновлённое имя','current_password':'wrong'})
  self.assertEqual(status,422);self.assertIn('Текущий пароль',body)
  status,body,url=self.request(self.b,'/profile',{'csrf':csrf,'revision':target['revision'],'login':'changed','display':'Обновлённое имя','current_password':'test-password','new_password':'new-password','confirm_password':'new-password'})
  self.assertIn('/login',url);self.assertEqual(s.authenticate('changed','new-password')['id'],uid)
  self.assertIn('/login',self.request(self.b,'/runs')[2])
  admincsrf=self.csrf(self.a,'/admin')
  with s.db() as c:revision=c.execute('SELECT revision FROM users WHERE id=?',(uid,)).fetchone()[0]
  self.assertEqual(self.request(self.a,'/admin/users/'+uid+'/edit')[0],200)
  status,body,_=self.request(self.a,'/admin/users/'+uid+'/edit',{'csrf':admincsrf,'revision':revision,'login':'changed','display':'Админ команды','status':'active','admin':'1','new_password':'admin-reset-pass','confirm_password':'admin-reset-pass'})
  self.assertEqual(status,200,body);self.assertEqual(s.authenticate('changed','admin-reset-pass')['admin'],1)
 def test_resource_delete_restore_purge_and_permissions_http(self):
  uid=self.approve()
  with s.db() as c:
   owner=dict(c.execute('SELECT * FROM users WHERE id=?',(uid,)).fetchone())
   rid=s.create(c,owner,'config','Удаляемая конфигурация','',{},'public')
   c.execute("UPDATE resources SET public_role='editor' WHERE id=?",(rid,))
  csrf=self.csrf(self.b);self.assertEqual(self.request(self.b,'/resources/'+rid+'/delete')[0],200)
  with s.db() as c:self.assertIsNone(s.resource(c,rid)['deleted_at'])
  self.assertEqual(self.request(self.b,'/resources/'+rid+'/delete',{'revision':'1'})[0],403)
  status,body,_=self.request(self.b,'/resources/'+rid+'/delete',{'csrf':csrf,'revision':'1'});self.assertEqual(status,200,body);self.assertIn('Удаляемая конфигурация',body)
  self.assertEqual(self.request(self.b,'/configurations/'+rid)[0],404)
  self.assertEqual(self.request(self.b,'/resources/'+rid+'/history')[0],200)
  status,body,_=self.request(self.b,'/resources/'+rid+'/restore',{'csrf':csrf,'revision':'1'});self.assertEqual(status,200,body)
  self.assertEqual(self.request(self.b,'/configurations/'+rid)[0],200)
  self.request(self.b,'/resources/'+rid+'/delete',{'csrf':csrf,'revision':'1'})
  self.assertEqual(self.request(self.b,'/resources/'+rid+'/purge',{'csrf':csrf,'revision':'1','confirm':'wrong'})[0],400)
  status,body,_=self.request(self.b,'/resources/'+rid+'/purge',{'csrf':csrf,'revision':'1','confirm':'Удаляемая конфигурация'});self.assertEqual(status,200,body)
  self.assertEqual(self.request(self.b,'/resources/'+rid+'/restore')[0],404)
  self.assertEqual(self.request(self.b,'/resources/'+rid+'/history')[0],200)
 def test_editor_cannot_delete_and_execution_urls_hidden(self):
  uid=self.approve();csrf=self.csrf(self.b)
  with s.db() as c:
   rid=s.create(c,self.admin,'run','Проверка удаления','',{'config':{}},'public');c.execute("UPDATE resources SET public_role='editor' WHERE id=?",(rid,))
   eid=s.uid();c.execute('INSERT INTO executions(id,run,actor,state,manifest,created,error) VALUES (?,?,?,?,?,0,?)',(eid,rid,self.admin['id'],'failed',json.dumps({'scripts':[]}),''))
  self.assertEqual(self.request(self.b,'/resources/'+rid+'/delete')[0],403)
  self.assertEqual(self.request(self.b,'/executions/'+eid+'/delete',{'csrf':csrf})[0],403)
  self.assertEqual(self.request(self.b,'/executions/'+eid)[0],200)
  admincsrf=self.csrf(self.a)
  status,body,_=self.request(self.a,'/executions/'+eid+'/delete',{'csrf':admincsrf});self.assertEqual(status,200,body)
  self.assertEqual(self.request(self.b,'/executions/'+eid)[0],404)
  self.assertEqual(self.request(self.b,'/api/executions/'+eid)[0],404)
  status,body,_=self.request(self.a,'/executions/'+eid+'/restore',{'csrf':admincsrf});self.assertEqual(status,200,body)
  self.assertEqual(self.request(self.b,'/executions/'+eid)[0],200)
  self.request(self.a,'/resources/'+rid+'/delete',{'csrf':admincsrf,'revision':'1'})
  self.assertEqual(self.request(self.b,'/executions/'+eid)[0],404)
 def test_report_pipeline_on_trusted_bundled_fixture(self):
  # This is explicitly a test of trusted bundled procedures, not a host execution mode.
  import service,subprocess,shutil
  from unittest.mock import patch
  service.import_demo(self.admin)
  with s.db() as c:r=dict(c.execute("SELECT * FROM resources WHERE kind='run'").fetchone())
  with patch.object(sandbox,'available',return_value=(True,'Test fixture')),patch.object(sandbox,'launch'):
   eid=service.execute(self.admin,r['id'])
  folder=s.STATE/'executions'/eid;result=folder/'result';result.mkdir()
  shutil.copytree(folder/'input/scripts',result/'scripts')
  cfg=json.loads((folder/'input/config.json').read_text())
  with s.db() as c:
   e=c.execute('SELECT manifest FROM executions WHERE id=?',(eid,)).fetchone();rid=json.loads(e[0])['dataset']['resource'];data=s.resource(c,rid)
  cfg['dataset']=str(s.STATE/'objects'/json.loads(data['body'])['folder']);(result/'config.json').write_text(json.dumps(cfg))
  completed=subprocess.run([sys.executable,str(s.ROOT/'engine.py'),str(result)],capture_output=True,timeout=90)
  self.assertEqual(completed.returncode,0,completed.stderr.decode());shutil.copytree(result,folder/'artifacts');sandbox.finish(eid,'done')
  summary=json.loads((result/'summary.json').read_text());self.assertEqual(summary['changed_apps'],502);self.assertEqual(summary['decision_changed'],385)
  for path in ['', '/pivot', '/checks', '/applications', '/applications/I32554354', '/export']:
   code,body,_=self.request(self.a,'/executions/'+eid+path);self.assertEqual(code,200,(path,body));self.assertIn('<html',body)
  self.assertIn('502',self.request(self.a,'/executions/'+eid)[1])
  uid=self.approve()
  self.assertEqual(self.request(self.b,'/executions/'+eid+'/checks')[0],200)
  manifest=json.loads(e[0]);sid=manifest['scripts'][0]['resource']
  self.assertEqual(self.request(self.b,'/executions/'+eid+'/code?script='+sid)[0],403)
  # Revocation applies to already-created execution URLs as well.
  with s.db() as c:c.execute("UPDATE resources SET visibility='private' WHERE id=?",(r['id'],))
  self.assertEqual(self.request(self.b,'/executions/'+eid+'/checks')[0],403)
if __name__=='__main__':unittest.main()
