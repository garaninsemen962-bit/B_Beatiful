"""Optional REAL sandbox integration. DD_TEST_DOCKER=1 enables it after building runner."""
import json,os,tempfile,time,unittest,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import store as s,service,sandbox
@unittest.skipUnless(os.environ.get('DD_TEST_DOCKER')=='1','Docker runtime check is opt-in; set DD_TEST_DOCKER=1')
class DockerRuntimeTest(unittest.TestCase):
 def test_full_bundled_run_inside_container(self):
  old=s.STATE
  with tempfile.TemporaryDirectory() as tmp:
   try:
    s.STATE=Path(tmp);sandbox.STATE=s.STATE;s.init();user=s.register('admin','Admin','test-password',True);service.import_demo(user)
    ok,message=sandbox.available();self.assertTrue(ok,message)
    with s.db() as c:rid=c.execute("SELECT id FROM resources WHERE kind='run'").fetchone()[0]
    eid=service.execute(user,rid);start=time.monotonic()
    while time.monotonic()-start<180:
     with s.db() as c:e=dict(c.execute('SELECT * FROM executions WHERE id=?',(eid,)).fetchone())
     if e['state'] in ('done','failed'):break
     time.sleep(.5)
    self.assertEqual(e['state'],'done',e.get('error','timeout'))
    summary=json.loads((s.STATE/'executions'/eid/'artifacts/summary.json').read_text())
    self.assertEqual(summary['changed_apps'],502);self.assertEqual(summary['decision_changed'],385)
   finally:s.STATE=old;sandbox.STATE=old
