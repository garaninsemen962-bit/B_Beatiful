"""Private prompt defaults, per-request override, migration and immutable snapshots."""
import json,os,sqlite3,time,unittest
from unittest.mock import patch
import test_review_features as helper
import store as s,ai_prompts,ai_review,lifecycle
class PromptTests(unittest.TestCase):
 setUp=helper.ReviewTests.setUp
 tearDown=helper.ReviewTests.tearDown
 client=helper.ReviewTests.client
 request=helper.ReviewTests.request
 csrf=helper.ReviewTests.csrf
 approve=helper.ReviewTests.approve
 fixture=helper.ReviewTests.fixture
 def wait(self,aid):
  for _ in range(200):
   with s.db() as c:row=dict(c.execute('SELECT * FROM analyses WHERE id=?',(aid,)).fetchone())
   if row['state'] in ('done','failed'):break
   time.sleep(.01)
  self.assertEqual(row['state'],'done',row['error']);return row
 def test_personal_preference_is_private_and_conflicts_keep_text(self):
  uid=self.approve();csrf=self.csrf(self.a);token=self.csrf(self.b)
  original=json.loads(self.request(self.a,'/api/me/ai-prompt')[1]);self.assertEqual(original['revision'],0)
  text='Личный промпт администратора <script>alert(1)</script>.'
  status,body,_=self.request(self.a,'/api/me/ai-prompt',{'csrf':csrf,'revision':0,'system_prompt':text});self.assertEqual(status,200,body);self.assertEqual(json.loads(body)['revision'],1)
  member=json.loads(self.request(self.b,'/api/me/ai-prompt')[1]);self.assertFalse(member['custom']);self.assertNotIn(text,member['text'])
  status,body,_=self.request(self.b,'/api/me/ai-prompt',{'csrf':token,'revision':0,'system_prompt':'Промпт участника','user_id':self.admin['id']});self.assertEqual(status,200,body)
  self.assertEqual(json.loads(self.request(self.a,'/api/me/ai-prompt')[1])['text'],text)
  status,body,_=self.request(self.a,'/profile');self.assertEqual(status,200);self.assertIn('data-personal-prompt',body);self.assertIn('&lt;script&gt;',body);self.assertNotIn('<script>alert(1)</script>',body)
  status,body,_=self.request(self.a,'/profile/ai-prompt',{'csrf':csrf,'prompt_revision':0,'system_prompt':'Не потерять мой текст','action':'save'});self.assertEqual(status,422);self.assertIn('Не потерять мой текст',body);self.assertIn('другой вкладке',body)
  self.assertEqual(self.request(self.a,'/api/me/ai-prompt',{'revision':1,'system_prompt':'bad'})[0],403)
  status,body,_=self.request(self.a,'/api/me/ai-prompt',{'csrf':csrf,'revision':1,'action':'reset'});self.assertEqual(status,200);self.assertFalse(json.loads(body)['custom']);self.assertEqual(json.loads(body)['text'],ai_prompts.DEFAULT_SYSTEM)
  with s.db() as c:
   events=[x[0] for x in c.execute("SELECT details FROM audit WHERE action LIKE '%личного промпта Gemini'")]
  self.assertTrue(events);self.assertNotIn(text,' '.join(events))
 def test_snapshot_changes_cache_and_ignores_later_profile_edits(self):
  eid,_,_,_=self.fixture();api='/api/executions/'+eid+'/ai';csrf=self.csrf(self.a)
  ai_prompts.save(self.admin,'Разбери причины подробно.',0);calls=[]
  def fake(model,payload,system=None):calls.append(system);return helper.detailed_response()
  with patch.dict(os.environ,{'GEMINI_API_KEY':'fake','GEMINI_MODEL':'gemini-3.5-flash-lite'}),patch.object(ai_review,'transport',side_effect=fake):
   first=ai_review.start(self.admin,eid,prompt_revision=1);row=self.wait(first);config=json.loads(row['prompt_config'])
   self.assertEqual(config['source'],'personal');self.assertEqual(config['text'],'Разбери причины подробно.');self.assertEqual(calls[0],config['system']);self.assertIn(ai_prompts.CONTRACT,calls[0])
   self.assertEqual(ai_review.start(self.admin,eid,prompt_revision=1),first);self.assertEqual(len(calls),1)
   second=ai_review.start(self.admin,eid,prompt_mode='request',system_prompt='Сделай акцент на массивах.');second_row=self.wait(second);self.assertNotEqual(second,first)
   third=ai_review.start(self.admin,eid,prompt_mode='builtin');self.wait(third);self.assertNotEqual(third,second);self.assertTrue(calls[2].startswith(ai_prompts.DEFAULT_SYSTEM))
   ai_prompts.save(self.admin,'Другой личный текст.',1)
   with self.assertRaisesRegex(ValueError,'уже изменён'):ai_review.start(self.admin,eid,prompt_revision=1)
   with s.db() as c:self.assertEqual(c.execute('SELECT prompt_config FROM analyses WHERE id=?',(first,)).fetchone()[0],row['prompt_config'])
   status,body,_=self.request(self.a,api+'?analysis='+second);self.assertEqual(status,200,body);self.assertIn('Сделай акцент на массивах.',json.loads(body)['html']);self.assertIn('prompt-snapshot',json.loads(body)['html'])
  with self.assertRaises(sqlite3.IntegrityError):
   with s.db() as c:c.execute("UPDATE analyses SET prompt_config='{}' WHERE id=?",(first,))
 def test_request_api_profile_fallback_validation_and_shared_snapshot(self):
  eid,rid,_,_=self.fixture();csrf=self.csrf(self.a);api='/api/executions/'+eid+'/ai'
  with patch.dict(os.environ,{'GEMINI_API_KEY':'fake'}),patch.object(ai_review,'transport',return_value=helper.detailed_response()):
   status,body,_=self.request(self.a,api,{'csrf':csrf,'prompt_mode':'request','system_prompt':'Мой запрос <b>буквально</b>','model':'gemini-3.5-flash-lite'});self.assertIn(status,(200,202),body);aid=json.loads(body)['analysis'];self.wait(aid)
   self.assertEqual(self.request(self.a,api,{'csrf':csrf,'prompt_mode':'request','system_prompt':''})[0],400)
  self.approve()
  with s.db() as c:c.execute("UPDATE resources SET visibility='public' WHERE id=?",(rid,))
  status,body,_=self.request(self.b,api+'?analysis='+aid);self.assertEqual(status,200);self.assertIn('&lt;b&gt;буквально&lt;/b&gt;',json.loads(body)['html'])
  status,body,_=self.request(self.a,'/profile/ai-prompt',{'csrf':csrf,'prompt_revision':0,'system_prompt':'Личный текст без JavaScript','action':'save'});self.assertEqual(status,200,body);self.assertIn('Личный текст без JavaScript',body)
  for text in ('', 'x'*(ai_prompts.MAX_CHARS+1)):
   with self.assertRaises(ValueError):ai_prompts.save(self.admin,text,1)
  lifecycle.execution_action(self.admin,eid,'delete');lifecycle.execution_action(self.admin,eid,'purge',eid)
  with s.db() as c:self.assertIsNone(c.execute('SELECT prompt_config FROM analyses WHERE id=?',(aid,)).fetchone()[0])
 def test_upgrade_old_schema_backup_and_legacy_unknown_prompt(self):
  with s.db() as c:
   c.execute('DROP TRIGGER analysis_prompt_immutable');c.execute('ALTER TABLE analyses DROP COLUMN prompt_config');c.execute('DROP TABLE ai_preferences')
  before=list((s.STATE/'backups').glob('*.sqlite')) if (s.STATE/'backups').exists() else []
  s.init();s.init();after=list((s.STATE/'backups').glob('*.sqlite'));self.assertEqual(len(after),len(before)+1)
  backup=next(x for x in after if x not in before)
  with sqlite3.connect(backup) as c:self.assertNotIn('prompt_config',{x[1] for x in c.execute('PRAGMA table_info(analyses)')})
  self.assertEqual(s.authenticate('admin','test-password')['id'],self.admin['id']);self.assertEqual(ai_prompts.read(self.admin)['revision'],0)
  import pages
  self.assertIn('не сохранялся',pages.prompt_snapshot({'prompt_config':None}))
if __name__=='__main__':unittest.main()
