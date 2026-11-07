"""Root console is opt-in, locally provisioned and session-unlocked; operations use temp files."""
import json,os,shlex,sys,time,unittest
from unittest.mock import patch
import test_http as helper
import store as s,accounts,root_admin
class RootTests(unittest.TestCase):
 setUp=helper.HttpTests.setUp
 tearDown=helper.HttpTests.tearDown
 client=helper.HttpTests.client
 request=helper.HttpTests.request
 csrf=helper.HttpTests.csrf
 approve=helper.HttpTests.approve
 def root(self,unlock=True):
  user=s.create_root('Root operator','root-test-password');client=self.client();self.request(client,'/login',{'login':'root','password':'root-test-password'});csrf=self.csrf(client)
  self.server.root_console_enabled=True
  if unlock:
   status,body,_=self.request(client,'/root/unlock',{'csrf':csrf,'password':'root-test-password'});self.assertEqual(status,200,body);self.assertIn('Файлы сервера',body)
  return user,client,csrf
 def wait(self,client,key):
  for _ in range(100):
   status,body,_=self.request(client,'/api/root/commands/'+key);self.assertEqual(status,200,body);result=json.loads(body)
   if result['state']=='done':return result
   time.sleep(.05)
  self.fail('command did not finish')
 def test_root_is_not_a_login_rename_or_admin_checkbox(self):
  with self.assertRaises(ValueError):s.register('root','fake','test-password',True)
  user,client,csrf=self.root(False)
  self.assertEqual(self.request(self.a,'/root')[0],403);self.assertEqual(self.request(client,'/api/root/files')[0],403)
  self.assertEqual(self.request(client,'/root/unlock',{'csrf':csrf,'password':'wrong'})[0],403)
  with self.assertRaises(PermissionError):accounts.update(self.admin,user['id'],{'revision':0,'login':'root','display':'Hijack'},True)
  with self.assertRaises(ValueError):accounts.update(self.admin,self.admin['id'],{'revision':0,'login':'root','display':'fake','current_password':'test-password'})
  accounts.update(self.admin,self.admin['id'],{'revision':0,'login':'admin','display':'Admin','server_root':'1'})
  with s.db() as c:self.assertFalse(s.is_root(dict(c.execute('SELECT * FROM users WHERE id=?',(self.admin['id'],)).fetchone())))
  self.server.root_console_enabled=False;self.assertEqual(self.request(client,'/api/root/files')[0],403)
  self.server.root_console_enabled=True
  self.request(client,'/root/unlock',{'csrf':csrf,'password':'root-test-password'})
  with s.db() as c:c.execute('UPDATE users SET server_root=0 WHERE id=?',(user['id'],))
  self.assertEqual(self.request(client,'/api/root/files')[0],403)
 def test_unlock_csrf_expiry_and_local_recovery(self):
  user,client,csrf=self.root();self.assertEqual(self.request(client,'/api/root/files',{'operation':'mkdir','path':str(s.STATE),'name':'bad'})[0],403)
  with s.db() as c:c.execute('UPDATE sessions SET root_until=0 WHERE user_id=?',(user['id'],))
  self.assertEqual(self.request(client,'/api/root/files')[0],403)
  self.request(client,'/root/unlock',{'csrf':csrf,'password':'root-test-password'})
  user2=s.create_root('Recreated root','new-root-test-password');self.assertEqual(user2['id'],user['id']);self.assertIn('/login',self.request(client,'/runs')[2])
 def test_files_write_conflict_backup_upload_move_and_trash(self):
  user,client,csrf=self.root();file=s.STATE/'test.txt';file.write_text('before')
  status,body,_=self.request(client,'/api/root/text?'+__import__('urllib.parse',fromlist=['urlencode']).urlencode({'path':str(file)}));self.assertEqual(status,200,body);info=json.loads(body)
  bad={'csrf':csrf,'operation':'write','path':str(file),'stamp':'bad','text':'lost'};self.assertEqual(self.request(client,'/api/root/files',bad)[0],400);self.assertEqual(file.read_text(),'before')
  bad.update(stamp=info['stamp'],text='after');status,body,_=self.request(client,'/api/root/files',bad);self.assertEqual(status,200,body);data=json.loads(body);self.assertEqual(file.read_text(),'after');self.assertEqual(__import__('pathlib').Path(data['backup']).read_text(),'before')
  moved=s.STATE/'moved.txt';status,body,_=self.request(client,'/api/root/files',{'csrf':csrf,'operation':'move','path':str(file),'stamp':data['stamp'],'destination':str(moved)});self.assertEqual(status,200,body);self.assertTrue(moved.exists())
  stamp=root_admin.stamp(moved);status,body,_=self.request(client,'/api/root/files',{'csrf':csrf,'operation':'trash','path':str(moved),'stamp':stamp});self.assertEqual(status,200,body);self.assertFalse(moved.exists());self.assertTrue(__import__('pathlib').Path(json.loads(body)['destination']).exists())
  status,body,_=self.request(client,'/api/root/files',{'csrf':csrf,'operation':'upload','path':str(s.STATE)},{'file':('upload.txt',b'uploaded')});self.assertEqual(status,200,body);self.assertEqual((s.STATE/'upload.txt').read_text(),'uploaded')
  self.assertEqual(self.request(client,'/api/root/files',{'csrf':csrf,'operation':'trash','path':str(s.STATE),'stamp':root_admin.stamp(s.STATE)})[0],400)
 def test_sql_readonly_write_backup_and_table_browser(self):
  user,client,csrf=self.root();db=str(s.STATE/'app.sqlite');url='/api/root/sql'
  status,body,_=self.request(client,url,{'csrf':csrf,'path':db,'sql':'SELECT login FROM users ORDER BY login'});self.assertEqual(status,200,body);self.assertIn(['root'],json.loads(body)['rows'])
  self.assertEqual(self.request(client,url,{'csrf':csrf,'path':db,'sql':"UPDATE users SET display='bad'"})[0],400)
  status,body,_=self.request(client,url,{'csrf':csrf,'path':db,'sql':'CREATE TABLE root_test(value TEXT)','write':'1'});self.assertEqual(status,200,body);self.assertTrue(__import__('pathlib').Path(json.loads(body)['backup']).exists())
  status,body,_=self.request(client,'/api/root/schema?'+__import__('urllib.parse',fromlist=['urlencode']).urlencode({'path':db}));self.assertEqual(status,200,body);self.assertIn('root_test',[x['name'] for x in json.loads(body)['tables']])
  status,body,_=self.request(self.a,url,{'csrf':self.csrf(self.a),'path':db,'sql':'SELECT * FROM users'});self.assertEqual(status,403)
 def test_commands_output_limit_cancel_and_nonroot_denial(self):
  user,client,csrf=self.root()
  status,body,_=self.request(client,'/api/root/commands',{'csrf':csrf,'command':'echo root-console-test','cwd':str(s.STATE),'timeout':5});self.assertEqual(status,202,body);key=json.loads(body)['id'];result=self.wait(client,key);self.assertEqual(result['exit_code'],0);self.assertIn('root-console-test',result['output'])
  self.assertEqual(self.request(self.a,'/api/root/commands/'+key)[0],403)
  if os.name=='posix':
   command=shlex.quote(sys.executable)+" -c 'import time; time.sleep(5)'"
   status,body,_=self.request(client,'/api/root/commands',{'csrf':csrf,'command':command,'cwd':str(s.STATE),'timeout':10});key=json.loads(body)['id'];self.request(client,'/api/root/commands/'+key,{'csrf':csrf});result=self.wait(client,key);self.assertEqual(result['reason'],'Отменено')
   with patch.object(root_admin,'MAX_OUTPUT',64):
    command=shlex.quote(sys.executable)+''' -c 'print("x"*10000)' '''
    status,body,_=self.request(client,'/api/root/commands',{'csrf':csrf,'command':command,'cwd':str(s.STATE),'timeout':5});result=self.wait(client,json.loads(body)['id']);self.assertTrue(result['truncated']);self.assertLessEqual(len(result['output']),64)
 def test_sql_read_mode_denies_attach_and_write_pragmas(self):
  root,client,csrf=self.root()
  path=str(s.STATE/'app.sqlite');outside=s.STATE/'readonly-attach.sqlite'
  for sql in ("ATTACH DATABASE '"+str(outside)+"' AS other",'PRAGMA user_version=42'):
   status,body,_=self.request(client,'/api/root/sql',{'csrf':csrf,'path':path,'sql':sql});self.assertNotEqual(status,200,body)
  self.assertFalse(outside.exists())
 def test_delete_restore_user_keeps_authorship_and_blocks_login(self):
  uid=self.approve();csrf=self.csrf(self.a)
  with s.db() as c:
   member=dict(c.execute('SELECT * FROM users WHERE id=?',(uid,)).fetchone());resource=s.create(c,member,'config','Keep authored resource','',{})
  status,body,_=self.request(self.a,'/admin/users/'+uid+'/delete',{'csrf':csrf,'revision':member['revision']});self.assertEqual(status,200,body);self.assertIn('/login',self.request(self.b,'/runs')[2])
  with self.assertRaises(PermissionError):s.authenticate('member','test-password')
  with s.db() as c:
   self.assertEqual(s.resource(c,resource)['owner'],uid);deleted=dict(c.execute('SELECT * FROM users WHERE id=?',(uid,)).fetchone());self.assertEqual(deleted['status'],'deleted')
  self.assertIn('member',self.request(self.a,'/admin?deleted=1')[1]);self.assertNotIn('member',self.request(self.a,'/admin')[1])
  status,body,_=self.request(self.a,'/admin/users/'+uid+'/restore',{'csrf':csrf,'revision':deleted['revision']});self.assertEqual(status,200,body);self.assertEqual(s.authenticate('member','test-password')['status'],'blocked')
  with self.assertRaises(ValueError):accounts.delete_user(self.admin,self.admin['id'],0)
  root,_,_=self.root()
  with self.assertRaises(PermissionError):accounts.delete_user(self.admin,root['id'],0)
if __name__=='__main__':unittest.main()
