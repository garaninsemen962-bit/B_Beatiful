"""Whole-pair exclusions, diagnostics and preservation of genuine attribute changes."""
import hashlib,json,shutil,sqlite3,tempfile,unittest
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import engine,uploads,store as s,reports
import test_http as http_helpers
import test_review_features as review_helpers

class InputQualityTests(unittest.TestCase):
 def setUp(self):self.tmp=tempfile.TemporaryDirectory();self.base=Path(self.tmp.name)
 def tearDown(self):self.tmp.cleanup()
 def run_fixture(self,docs,normalizer_extra='',comparator_extra=''):
  folder=self.base/'run';folder.mkdir();scripts=folder/'scripts';scripts.mkdir();data=self.base/'dataset'/'RESULTS'
  for source in ('BEFORE','AFTER'):
   target=data/('BEFORE_MASTER' if source=='BEFORE' else 'AFTER_BETA');target.mkdir(parents=True)
   for app,doc in docs.get(source,{}).items():(target/(app+'.json')).write_bytes(doc if isinstance(doc,bytes) else json.dumps(doc).encode())
  for name in ('normalize','compare','aggregate'):shutil.copy(s.ROOT/'procedures'/(name+'.py'),scripts)
  for name,extra in [('normalize',normalizer_extra),('compare',comparator_extra)]:
   with (scripts/(name+'.py')).open('a') as out:out.write('\n'+extra)
  config={'dataset':str(data.parent),'normalizer':'normalize.py','comparator':'compare.py','aggregator':'aggregate.py','checks':[]}
  (folder/'config.json').write_text(json.dumps(config));engine.process(folder)
  return folder,json.loads((folder/'summary.json').read_text()),json.loads((folder/'input_errors.json').read_text())
 def test_missing_corrupt_both_sides_and_real_new_lost(self):
  docs={'BEFORE':{'good':{'gone':1,'keep':None},'only_before':{'x':1},'broken':b'{','both':b'','empty':{}},'AFTER':{'good':{'new':None,'keep':False},'only_after':{'x':2},'broken':{'x':1},'both':b'\xff','empty':{}}}
  folder,summary,quality=self.run_fixture(docs)
  self.assertEqual((summary['input_apps'],summary['total_apps'],summary['excluded_apps']),(6,2,4));self.assertEqual(summary['input_errors'],{'BEFORE':3,'AFTER':2})
  db=sqlite3.connect(folder/'mart.sqlite');m=engine.Mart(db)
  self.assertEqual(m.app_ids(),['good']);self.assertEqual(m.count(change_type=['ATTRIBUTE_NEW','ATTRIBUTE_LOST']),2)
  self.assertEqual({x['APP_ID'] for x in m.input_errors()},{'only_before','only_after','broken','both'})
  self.assertEqual({x['APP_ID'] for x in m.segments()},{'good','empty'})
  checks=json.loads((folder/'checks.json').read_text())['rows'];self.assertEqual([(x['CNT'],x['TOTAL']) for x in checks],[(3,6),(2,6)])
  self.assertEqual((folder/'parse_errors.log').read_text().count('both'),2);self.assertTrue(quality['available']);db.close()
 def test_all_invalid_and_whole_missing_slice_complete_with_quality_fail(self):
  folder,summary,_=self.run_fixture({'BEFORE':{'a':{},'b':b'[]'}})
  self.assertEqual((summary['total_apps'],summary['excluded_apps']),(0,2));self.assertEqual(json.loads((folder/'status.json').read_text())['state'],'done')
  checks=json.loads((folder/'checks.json').read_text())['rows'];self.assertEqual(checks[-1]['LABEL'],'Нет сопоставимых пар');self.assertEqual(checks[-1]['RESULT'],'FAIL')
 def test_strict_parser_and_utf8_bom(self):
  self.assertEqual(engine.strict_document(b'\xef\xbb\xbf{"a":null}'),{'a':None})
  for raw in (b'',b'null',b'[]',b'{"a":NaN}',b'{"a":1,"a":2}',b'\xff',b'{"a":Infinity}'):
   with self.subTest(raw=raw),self.assertRaises((ValueError,TypeError,UnicodeError)):engine.strict_document(raw)
 def test_normalizer_validation_and_pair_data_errors(self):
  extra="def validate(doc, app_id, source, params):\n    return not doc.get('invalid',False)\n"
  comp="original_compare=compare\ndef compare(rows,params):\n    if any(x['APP_ID']=='pair_error' for x in rows):raise ValueError('bad pair')\n    return original_compare(rows,params)\n"
  docs={source:{'valid':{'x':1},'validation':{'invalid':source=='AFTER'},'pair_error':{'x':1},'unknown_array':{'x':[1]}} for source in ('BEFORE','AFTER')}
  folder,summary,quality=self.run_fixture(docs,extra,comp)
  self.assertEqual(summary['total_apps'],1);self.assertEqual(summary['pair_errors'],1)
  self.assertEqual(len(quality['errors']),3);self.assertEqual(quality['pair_errors'][0]['STAGE'],'compare')
  check=engine.load(s.ROOT/'checks/check_parse_errors.py');db=sqlite3.connect(folder/'mart.sqlite');self.assertEqual(check.run(engine.Mart(db),{})[0]['TOTAL'],4);db.close()
 def test_unexpected_script_bug_still_fails_execution(self):
  with self.assertRaises(NameError):self.run_fixture({s:{'a':{'x':1}} for s in ('BEFORE','AFTER')},"def normalize(*args):\n    raise NameError('script bug')\n")
  self.assertEqual(json.loads((self.base/'run/status.json').read_text())['state'],'failed')
 def test_recheck_preserves_readonly_quality_and_old_mart_unknown(self):
  folder,summary,quality=self.run_fixture({'BEFORE':{'a':{'x':1},'b':b'{'},'AFTER':{'a':{'x':2}}})
  mart=folder/'mart.sqlite';digest=hashlib.sha256(mart.read_bytes()).hexdigest();out=self.base/'recheck';out.mkdir();(out/'config.json').write_text(json.dumps({'mode':'checks','source_mart':str(mart),'checks':[]}))
  engine.process(out);self.assertEqual(json.loads((out/'input_errors.json').read_text()),quality);self.assertEqual(hashlib.sha256(mart.read_bytes()).hexdigest(),digest)
  db=sqlite3.connect(mart);db.executescript('DROP TABLE input_status;DROP TABLE pair_errors;');db.close();old=self.base/'old';old.mkdir();(old/'config.json').write_text((out/'config.json').read_text());engine.process(old)
  self.assertFalse(json.loads((old/'input_errors.json').read_text())['available']);self.assertEqual(json.loads((old/'checks.json').read_text())['rows'],[])
 def test_zip_keeps_bad_json_for_diagnostics(self):
  import io,zipfile
  raw=io.BytesIO()
  with zipfile.ZipFile(raw,'w') as z:z.writestr('RESULTS/BEFORE/A.json',b'{')
  result=uploads.dataset_zip(raw.getvalue(),self.base/'upload');self.assertEqual(result['invalid_responses'],1);self.assertEqual((self.base/'upload/RESULTS/BEFORE_MASTER/A.json').read_bytes(),b'{');self.assertEqual(result['slices']['AFTER_BETA'],0)

class InputQualityHttpTests(unittest.TestCase):
 setUp=http_helpers.HttpTests.setUp
 tearDown=http_helpers.HttpTests.tearDown
 client=http_helpers.HttpTests.client
 request=http_helpers.HttpTests.request
 csrf=http_helpers.HttpTests.csrf
 approve=http_helpers.HttpTests.approve
 fixture=review_helpers.ReviewTests.fixture
 def test_excluded_reports_drill_download_and_acl(self):
  from urllib.parse import urlencode
  eid,rid,_,_=self.fixture({'BEFORE':{'ok':{'gone':1},'bad':b'{','absent':{'x':1}},'AFTER':{'ok':{'new':2},'bad':{'x':1}}})
  base='/executions/'+eid
  status,body,_=self.request(self.a,base);self.assertEqual(status,200);self.assertIn('Входных заявок: 3',body);self.assertIn('исключено: 2',body)
  status,body,_=self.request(self.a,base+'/applications?excluded=1');self.assertEqual(status,200);self.assertIn('2 заявок',body);self.assertIn('/applications/bad?',body)
  for app,reason in [('bad','INVALID_JSON'),('absent','MISSING')]:
   status,body,_=self.request(self.a,base+'/applications/'+app+'?excluded=1');self.assertEqual(status,200);self.assertIn('Заявка исключена',body);self.assertIn(reason,body)
  status,body,_=self.request(self.a,base+'/applications?'+urlencode({'ids':'["bad"]'}));self.assertEqual(status,200);self.assertIn('1 заявок',body)
  self.assertEqual(self.request(self.a,base+'/applications/unknown')[0],404)
  status,body,_=self.request(self.a,base+'/input-errors');self.assertEqual(status,200);self.assertEqual(json.loads(body)['input_apps'],3)
  self.approve();self.assertEqual(self.request(self.b,base+'/input-errors')[0],403)
  status,body,_=self.request(self.a,base+'/checks');self.assertEqual(status,200);self.assertIn('Ошибки исполнения BEFORE',body)
 def test_all_excluded_empty_pivot_and_import_warning(self):
  eid,_,_,_=self.fixture({'BEFORE':{'a':b'{','b':{}},'AFTER':{'a':b'null'}})
  base='/executions/'+eid
  for route in ('','/checks','/pivot','/applications?excluded=1','/ai'):
   status,body,_=self.request(self.a,base+route);self.assertEqual(status,200,body)
  with s.db() as c:dataset=c.execute("SELECT id FROM resources WHERE kind='dataset'").fetchone()[0]
  status,body,_=self.request(self.a,'/datasets/'+dataset);self.assertEqual(status,200);self.assertIn('Есть повреждённые ответы',body)
