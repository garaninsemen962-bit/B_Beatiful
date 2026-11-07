import json,sqlite3,tempfile,sys
from pathlib import Path
from unittest import TestCase
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import store as s,accounts,lifecycle
class ManagementTests(TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.old=s.STATE;s.STATE=Path(self.tmp.name);s.init()
  self.admin=s.register('admin','Администратор','test-password',True)
  self.member=s.register('member','Участник','test-password')
  self.other=s.register('other','Другой','test-password')
  with s.db() as c:c.execute("UPDATE users SET status='active'")
  self.member['status']=self.other['status']='active'
 def tearDown(self):s.STATE=self.old;self.tmp.cleanup()
 def user(self,uid):
  with s.db() as c:return dict(c.execute('SELECT * FROM users WHERE id=?',(uid,)).fetchone())
 def create(self,kind='config',body=None,owner=None):
  with s.db() as c:return s.create(c,owner or self.member,kind,'Ресурс','Описание',body or {})
 def execution(self,rid,state='done',manifest=None):
  eid=s.uid()
  with s.db() as c:c.execute('INSERT INTO executions(id,run,actor,state,manifest,created,error) VALUES (?,?,?,?,?,0,?)',(eid,rid,self.member['id'],state,json.dumps(manifest or {}),''))
  folder=s.STATE/'executions'/eid;folder.mkdir();(folder/'test.txt').write_text('data');return eid
 def test_own_profile_and_password(self):
  uid=self.member['id'];token=s.session(self.member)
  revoke=accounts.update(self.member,uid,{'revision':'0','display':'Новое имя','admin':'1','status':'blocked'})
  self.assertFalse(revoke);self.assertEqual(self.user(uid)['admin'],0);self.assertEqual(self.user(uid)['status'],'active')
  self.assertEqual(self.user(uid)['display'],'Новое имя')
  with self.assertRaises(ValueError):accounts.update(self.member,uid,{'revision':'1','login':'new_login','current_password':'bad'})
  self.assertTrue(accounts.update(self.member,uid,{'revision':'1','login':'new_login','current_password':'test-password','new_password':'changed-password','confirm_password':'changed-password'}))
  self.assertEqual(s.authenticate('new_login','changed-password')['id'],uid)
  with s.db() as c:
   self.assertIsNone(c.execute('SELECT * FROM sessions WHERE token=?',(token,)).fetchone())
   text=' '.join(r[0] for r in c.execute('SELECT details FROM audit'));self.assertNotIn('changed-password',text)
 def test_admin_changes_and_last_admin_guard(self):
  uid=self.member['id'];s.session(self.member)
  self.assertTrue(accounts.update(self.admin,uid,{'revision':'0','admin':'1','status':'active','display':'Новый администратор'},True));self.assertEqual(self.user(uid)['admin'],1)
  with self.assertRaises(ValueError):accounts.update(self.admin,self.admin['id'],{'revision':'0','admin':'0','status':'active'},True)
  with self.assertRaises(ValueError):accounts.update(self.admin,self.admin['id'],{'revision':'0','admin':'1','status':'blocked'},True)
  with self.assertRaises(PermissionError):accounts.update(self.other,uid,{'revision':'1','admin':'1'},True)
  with self.assertRaises(PermissionError):accounts.update(self.other,uid,{'revision':'1','display':'attack'})
 def test_unique_login_conflict_and_fixed_audit_signature(self):
  uid=self.member['id']
  with self.assertRaises(ValueError):accounts.update(self.member,uid,{'revision':'0','login':'other','current_password':'test-password'})
  accounts.update(self.member,uid,{'revision':'0','display':'Новое имя'})
  with self.assertRaises(ValueError):accounts.update(self.member,uid,{'revision':'0','display':'Потерянная правка'})
  with s.db() as c:
   first=c.execute('SELECT display FROM audit WHERE actor=? ORDER BY id LIMIT 1',(uid,)).fetchone()[0];self.assertEqual(first,'Участник')
 def test_resource_trash_restore_permissions(self):
  rid=self.create()
  with s.db() as c:c.execute("UPDATE resources SET visibility='public' WHERE id=?",(rid,))
  with self.assertRaises(PermissionError):lifecycle.resource_action(self.other,rid,'delete',1)
  lifecycle.resource_action(self.member,rid,'delete',1)
  self.assertFalse(any(r['id']==rid for r in s.visible(self.member)))
  with s.db() as c:
   with self.assertRaises(FileNotFoundError):s.resource(c,rid)
   self.assertEqual(c.execute('SELECT count(*) FROM versions WHERE resource=?',(rid,)).fetchone()[0],1)
  lifecycle.resource_action(self.member,rid,'restore',1)
  self.assertTrue(any(r['id']==rid for r in s.visible(self.other)))
 def test_file_purge_and_retained_history(self):
  name=s.uid()+'.py';path=s.STATE/'objects'/name;path.write_text('example')
  rid=self.create('script',{'file':name,'script_kind':'check'});lifecycle.resource_action(self.member,rid,'delete',1)
  with self.assertRaises(ValueError):lifecycle.resource_action(self.member,rid,'purge',1,'wrong')
  self.assertTrue(path.exists());self.assertFalse(lifecycle.resource_action(self.member,rid,'purge',1,'Ресурс'));self.assertFalse(path.exists())
  with self.assertRaises(ValueError):lifecycle.resource_action(self.member,rid,'restore',1)
  with s.db() as c:
   self.assertIsNotNone(s.resource(c,rid,True)['purged_at']);self.assertGreater(c.execute('SELECT count(*) FROM audit WHERE resource=?',(rid,)).fetchone()[0],1)
 def test_dependency_and_historical_version_protection(self):
  source=self.create('script',{'file':s.uid()+'.py'});config=self.create('config',{'checks':[source]})
  with s.db() as c:r=s.resource(c,config);s.revise(c,self.member,r,1,'Ресурс','',{'checks':[]})
  lifecycle.resource_action(self.member,source,'delete',1)
  with self.assertRaises(ValueError):lifecycle.resource_action(self.member,source,'purge',1,'Ресурс')
  lifecycle.resource_action(self.member,config,'delete',2)
  with self.assertRaises(ValueError):lifecycle.resource_action(self.member,source,'purge',1,'Ресурс')
  lifecycle.resource_action(self.member,config,'purge',2,'Ресурс');lifecycle.resource_action(self.member,source,'purge',1,'Ресурс')
 def test_execution_trash_purge_and_run_cascade(self):
  run=self.create('run',{'config':{}});eid=self.execution(run)
  lifecycle.execution_action(self.member,eid,'delete')
  self.assertTrue((s.STATE/'executions'/eid).exists());lifecycle.execution_action(self.member,eid,'restore')
  lifecycle.execution_action(self.member,eid,'delete')
  with self.assertRaises(ValueError):lifecycle.execution_action(self.member,eid,'purge','wrong')
  lifecycle.execution_action(self.member,eid,'purge',eid);self.assertFalse((s.STATE/'executions'/eid).exists())
  another=self.execution(run);lifecycle.resource_action(self.member,run,'delete',1);lifecycle.resource_action(self.member,run,'purge',1,'Ресурс')
  self.assertFalse((s.STATE/'executions'/another).exists())
  with s.db() as c:self.assertIsNotNone(c.execute('SELECT purged_at FROM executions WHERE id=?',(another,)).fetchone()[0])
 def test_active_execution_cannot_be_deleted(self):
  run=self.create('run',{'config':{}});eid=self.execution(run,'running')
  with self.assertRaises(ValueError):lifecycle.execution_action(self.member,eid,'delete')
  with self.assertRaises(ValueError):lifecycle.resource_action(self.member,run,'delete',1)
 def test_execution_manifest_blocks_input_purge(self):
  source=self.create('dataset',{'folder':s.uid()});run=self.create('run',{'config':{}});eid=self.execution(run,manifest={'dataset':{'resource':source}})
  lifecycle.resource_action(self.member,source,'delete',1)
  with self.assertRaises(ValueError):lifecycle.resource_action(self.member,source,'purge',1,'Ресурс')
  lifecycle.execution_action(self.member,eid,'delete');lifecycle.execution_action(self.member,eid,'purge',eid)
  lifecycle.resource_action(self.member,source,'purge',1,'Ресурс')
 def test_migration_backup_idempotent_and_login_preserved(self):
  with s.db() as c:
   c.execute('DROP INDEX resources_deleted')
   for table,cols in [('users',['revision']),('resources',['deleted_at','deleted_by','purged_at']),('executions',['deleted_at','deleted_by','purged_at'])]:
    for col in cols:c.execute('ALTER TABLE '+table+' DROP COLUMN '+col)
  s.init();self.assertEqual(s.authenticate('admin','test-password')['id'],self.admin['id'])
  backups=list((s.STATE/'backups').glob('*.sqlite'));self.assertEqual(len(backups),1)
  with sqlite3.connect(backups[0]) as c:self.assertNotIn('revision',{r[1] for r in c.execute('PRAGMA table_info(users)')})
  s.init();self.assertEqual(len(list((s.STATE/'backups').glob('*.sqlite'))),1)
