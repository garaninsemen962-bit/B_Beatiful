import io,json,os,shutil,tempfile,unittest,zipfile
from pathlib import Path
from unittest.mock import patch
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import store as s,service,sandbox
from uploads import dataset_zip
class PlatformTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.old=s.STATE;s.STATE=Path(self.tmp.name);sandbox.STATE=s.STATE;s.init()
  self.owner=s.register('owner','Владелец','test-password',True);self.reader=s.register('reader','Читатель','test-password');self.editor=s.register('editor','Редактор','test-password')
  with s.db() as c:c.execute("UPDATE users SET status='active' WHERE admin=0")
  self.reader['status']=self.editor['status']='active'
 def tearDown(self):s.STATE=self.old;sandbox.STATE=self.old;self.tmp.cleanup()
 def create(self,kind='script'):
  with s.db() as c:return s.create(c,self.owner,kind,'Название','Описание',{})
 def test_password_reset_revokes_sessions_preserves_resources(self):
  rid=self.create();token=s.session(self.owner)
  s.reset_password('owner','new-test-password')
  self.assertEqual(s.authenticate('owner','new-test-password')['id'],self.owner['id'])
  with self.assertRaises(PermissionError):s.authenticate('owner','test-password')
  with s.db() as c:
   self.assertEqual(c.execute('SELECT count(*) FROM sessions WHERE token=?',(token,)).fetchone()[0],0)
   self.assertEqual(s.resource(c,rid)['owner'],self.owner['id'])
   event=c.execute('SELECT * FROM audit ORDER BY id DESC LIMIT 1').fetchone();self.assertNotIn('new-test-password',event['details'])
  with self.assertRaises(ValueError):s.reset_password('owner','short')
  with self.assertRaises(ValueError):s.reset_password('missing','new-test-password')
  self.assertEqual(s.authenticate('owner','new-test-password')['admin'],1)
 def test_pending_registration(self):
  user=s.register('pending','Ожидает','test-password');rid=self.create()
  with s.db() as c:
   r=s.resource(c,rid);c.execute("UPDATE resources SET visibility='public' WHERE id=?",(rid,));self.assertIsNone(s.role(c,user,s.resource(c,rid)))
 def test_acl_private_named_public_and_download(self):
  rid=self.create()
  with s.db() as c:
   self.assertIsNone(s.role(c,self.reader,s.resource(c,rid)))
   c.execute("UPDATE resources SET visibility='named' WHERE id=?",(rid,));c.execute('INSERT INTO grants VALUES (?,?,?,?)',(rid,self.reader['id'],'reader',0));c.execute('INSERT INTO grants VALUES (?,?,?,?)',(rid,self.editor['id'],'editor',0))
   r=s.resource(c,rid);self.assertEqual(s.require(c,self.reader,r),'reader');self.assertFalse(s.can_download(c,self.reader,r));self.assertTrue(s.can_download(c,self.editor,r))
   with self.assertRaises(PermissionError):s.require(c,self.reader,r,'editor')
   c.execute("UPDATE resources SET visibility='public',public_role='reader' WHERE id=?",(rid,));self.assertEqual(s.role(c,self.editor,s.resource(c,rid)),'editor')
 def test_revision_conflict_and_audit(self):
  rid=self.create()
  with s.db() as c:
   r=s.resource(c,rid);s.revise(c,self.owner,r,1,'Новое','Описание',{'v':2})
   with self.assertRaises(ValueError):s.revise(c,self.owner,s.resource(c,rid),1,'Потеря','','')
   events=c.execute('SELECT * FROM audit WHERE resource=?',(rid,)).fetchall();self.assertEqual(events[-1]['actor'],self.owner['id'])
   with self.assertRaises(Exception):c.execute('UPDATE audit SET action=? WHERE resource=?',('spoof',rid))
   with self.assertRaises(Exception):c.execute('DELETE FROM versions WHERE resource=?',(rid,))
 def test_docstring_does_not_execute(self):
  code='"""Русское название\n\nОписание метрики."""\nraise RuntimeError("DO NOT EXECUTE")\ndef run(mart,params): return []\n'
  rid=service.save_script(self.owner,{'script_kind':'check'},code)
  with s.db() as c:
   r=s.resource(c,rid);self.assertEqual(r['name'],'Русское название');self.assertEqual(r['description'],'Описание метрики.')
 def archive(self,entries):
  out=io.BytesIO()
  with zipfile.ZipFile(out,'w') as z:
   for k,v in entries.items():z.writestr(k,v)
  return out.getvalue()
 def test_zip_import_and_traversal(self):
  data=self.archive({'root/RESULTS/BEFORE_MASTER/A.json':'{"x":1}','root/RESULTS/AFTER_BETA/A.json':'{"x":2}'})
  info=dataset_zip(data,s.STATE/'valid');self.assertEqual(info['apps'],1)
  with self.assertRaises(ValueError):dataset_zip(self.archive({'../outside':'bad'}),s.STATE/'invalid')
  single=dataset_zip(self.archive({'RESULTS/BEFORE_MASTER/A.json':'{}'}),s.STATE/'missing');self.assertEqual(single['slices']['AFTER_BETA'],0)
 def test_container_fail_closed(self):
  rid=self.create('run')
  with patch.object(sandbox,'available',return_value=(False,'Docker unavailable')):
   with self.assertRaises(ValueError):service.execute(self.owner,rid)
  with s.db() as c:self.assertEqual(c.execute('SELECT count(*) FROM executions').fetchone()[0],0)
 def test_container_flags(self):
  cmd=sandbox.command('test',s.STATE/'executions/test',s.STATE/'objects/data')
  for item in ['--network=none','--read-only','--user=65534:65534','--cap-drop=ALL','--security-opt=no-new-privileges','--pids-limit=64','--memory=1g','--cpus=2']:self.assertIn(item,cmd)
  self.assertFalse(any('docker.sock' in x or 'app.sqlite' in x for x in cmd));self.assertIn('readonly',str(cmd))
 def test_immutable_execution_manifest_and_reader_cannot_launch(self):
  service.import_demo(self.owner)
  with s.db() as c:
   r=dict(c.execute("SELECT * FROM resources WHERE kind='run'").fetchone())
  with patch.object(sandbox,'available',return_value=(True,'Ready')),patch.object(sandbox,'launch'):
   with self.assertRaises(PermissionError):service.execute(self.reader,r['id'])
   eid=service.execute(self.owner,r['id'])
  with s.db() as c:
   e=dict(c.execute('SELECT * FROM executions WHERE id=?',(eid,)).fetchone());manifest=json.loads(e['manifest'])
   self.assertEqual(len(manifest['scripts']),8);self.assertEqual(manifest['dataset']['revision'],1)
   cfg=json.loads(r['body'])['config'];cfg['normalize_params']={'exclude':[]}
   s.revise(c,self.owner,r,1,'Изменённый прогон','',{'config':cfg})
   old=json.loads(c.execute('SELECT manifest FROM executions WHERE id=?',(eid,)).fetchone()[0]);self.assertEqual(old['run_revision'],1);self.assertEqual(old['config']['normalize_params'],{})
if __name__=='__main__':unittest.main()
