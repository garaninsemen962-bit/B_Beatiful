"""Reproducible benchmark of ONLY the trusted bundled example, never uploaded code.
Run: python tests/benchmark.py
This is a developer measurement, not the production Docker runner.
"""
import json,shutil,subprocess,sys,time,urllib.parse
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from test_http import HttpTests
import store as s,service,sandbox,reports

def main():
 test=HttpTests();test.setUp()
 try:
  service.import_demo(test.admin)
  with s.db() as c:run=dict(c.execute("SELECT * FROM resources WHERE kind='run'").fetchone())
  with patch.object(sandbox,'available',return_value=(True,'')),patch.object(sandbox,'launch'):eid=service.execute(test.admin,run['id'])
  folder=s.STATE/'executions'/eid;artifacts=folder/'artifacts';artifacts.mkdir();shutil.copytree(folder/'input/scripts',artifacts/'scripts')
  cfg=json.loads((folder/'input/config.json').read_text())
  with s.db() as c:dataset=s.resource(c,json.loads(c.execute('SELECT manifest FROM executions WHERE id=?',(eid,)).fetchone()[0])['dataset']['resource'])
  cfg['dataset']=str(s.STATE/'objects'/json.loads(dataset['body'])['folder']);(artifacts/'config.json').write_text(json.dumps(cfg))
  start=time.perf_counter();r=subprocess.run([sys.executable,str(s.ROOT/'engine.py'),str(artifacts)],capture_output=True,timeout=180);elapsed=time.perf_counter()-start
  if r.returncode:raise RuntimeError(r.stderr.decode())
  sandbox.finish(eid,'done');reports.facts.cache_clear();base='/executions/'+eid
  timings={}
  for label,path in [('pivot_cold','/pivot'),('pivot_warm','/pivot'),('layout_change','/pivot?group=CHANGE_TYPE&columns=SOURCE'),('filter','/pivot?path=score'),('applications','/applications?'+urllib.parse.urlencode({'filter':json.dumps({'SOURCE':'AFTER'})})),('application_card','/applications/I32554354'),('checks','/checks'),('gemini_evidence','/ai')]:
   tick=time.perf_counter();status,body,_=test.request(test.a,base+path);timings[label]={'seconds':round(time.perf_counter()-tick,4),'status':status,'html_bytes':len(body.encode())}
   if status!=200:raise RuntimeError(label+': '+body[:200])
  with s.db() as c:check=json.loads(c.execute('SELECT manifest FROM executions WHERE id=?',(eid,)).fetchone()[0])['config']['checks'][0]
  tick=time.perf_counter()
  with patch.object(sandbox,'available',return_value=(True,'')),patch.object(sandbox,'launch'):child=service.recheck(test.admin,eid,{'checks':[check],'params':'{}'})
  copy_seconds=time.perf_counter()-tick;cf=s.STATE/'executions'/child;out=cf/'trusted-benchmark';out.mkdir();shutil.copytree(cf/'input/scripts',out/'scripts');config=json.loads((cf/'input/config.json').read_text());config['source_mart']=str(cf/'input/mart.sqlite');(out/'config.json').write_text(json.dumps(config))
  tick=time.perf_counter();r=subprocess.run([sys.executable,str(s.ROOT/'engine.py'),str(out)],capture_output=True,timeout=60);check_seconds=time.perf_counter()-tick
  if r.returncode:raise RuntimeError(r.stderr.decode())
  result={'environment':{'python':sys.version.split()[0],'platform':sys.platform,'execution':'host; trusted bundled code only; NOT a Docker timing','cpu_limit':'not set'},'full_worker_seconds':round(elapsed,3),'summary':reports.artifact(eid,'summary.json'),'stages':reports.artifact(eid,'status.json')['logs'],'http':timings,'recheck':{'snapshot_and_mart_copy_seconds':round(copy_seconds,3),'worker_seconds':round(check_seconds,3),'checks':1},'mart_bytes':(artifacts/'mart.sqlite').stat().st_size}
  print(json.dumps(result,ensure_ascii=False,indent=2))
 finally:test.tearDown()
if __name__=='__main__':main()
