"""Новые проверки (порог, новый стоп-фактор, различия без смены решения),
блок причин смены решения и локальный режим без Docker."""
import importlib.util,json,sqlite3,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from engine import Mart

def load(name):
    spec=importlib.util.spec_from_file_location(name,ROOT/'checks'/(name+'.py'));m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m

COLS=['APP_ID','PREFIX','CLASS','SUFFIX','ARRAY_KEY','KEY','PATH','VALUE_BEFORE','VALUE_AFTER','ADJUSTED_BEFORE','ADJUSTED_AFTER','DIFF_FLAG','CHANGE_TYPE']
def mart(rows,segments):
    db=sqlite3.connect(':memory:')
    db.execute('CREATE TABLE comparison('+','.join(COLS)+')');db.execute('CREATE TABLE segments(APP_ID,data)')
    for r in rows:db.execute('INSERT INTO comparison VALUES ('+','.join('?'*len(COLS))+')',[r.get(c,'') for c in COLS])
    for s in segments:db.execute('INSERT INTO segments VALUES (?,?)',(s['APP_ID'],json.dumps(s)))
    return Mart(db)
def row(app,path,before,after,akey='',ct='VALUE_CHANGED'):
    parts=path.split('.');return dict(APP_ID=app,PREFIX=parts[0],CLASS=parts[1] if len(parts)>2 else '',SUFFIX=parts[2] if len(parts)>3 else '',ARRAY_KEY=akey,KEY=parts[-1],PATH=path,VALUE_BEFORE=before,VALUE_AFTER=after,DIFF_FLAG=0 if ct=='SAME' else 1,CHANGE_TYPE=ct)
def seg(app,b,a,model=('M1','M1')):
    return dict(APP_ID=app,DECISION_BEFORE=b,DECISION_AFTER=a,DECISION_CHANGED=int(b!=a),PD_MODEL_BEFORE=model[0],PD_MODEL_AFTER=model[1],PRODUCT_DETAILS={'BEFORE':{'7':{'product_name':'NA_KARTU'}}})

class NewChecks(unittest.TestCase):
    def setUp(self):
        C='decisionResult.product_trials.cutOff.value';H='decisionResult.product_trials.rules.hit'
        self.m=mart([row('A1',C,'0.55','0.62','7'),row('A2',C,'0.55','0.62','7'),row('A3',C,'0.55','0.7','7'),
                     row('A4',H,'','true','7|DOCS_EXPIRED','ATTRIBUTE_NEW'),row('A5',H,'true','false','7|AGELOW@21'),
                     row('A6','decisionResult.scoring.value','0.4','0.5'),row('A7','decisionResult.decision','APPROVE','APPROVE',ct='SAME')],
                    [seg('A1','APPROVE','REJECT'),seg('A2','APPROVE','APPROVE'),seg('A3','APPROVE','REJECT'),seg('A4','APPROVE','REJECT'),
                     seg('A5','REJECT','APPROVE'),seg('A6','APPROVE','APPROVE'),seg('A7','APPROVE','APPROVE')])
    def test_cutoff_shift_grouped_by_product_and_values(self):
        rows=load('check_cutoff_shifted').run(self.m)
        self.assertEqual([(r['ARGUMENT'],r['CNT'],r['RESULT']) for r in rows],
            [('NA_KARTU: 0.55 → 0.62; решение сменилось у 1',2,'FAIL'),('NA_KARTU: 0.55 → 0.7; решение сменилось у 1',1,'FAIL')])
        self.assertEqual(rows[0]['APP_IDS'],['A1','A2']);self.assertEqual(rows[0]['CONFIDENCE'],'LOW')
    def test_new_stop_factor_only_new_hits(self):
        rows=load('check_stop_factor_new').run(self.m)
        self.assertEqual(len(rows),1);self.assertIn('DOCS_EXPIRED',rows[0]['ARGUMENT']);self.assertEqual(rows[0]['APP_IDS'],['A4'])
    def test_diff_without_decision_change(self):
        rows=load('check_diff_same_decision').run(self.m)
        self.assertEqual(rows[0]['APP_IDS'],['A2','A6']);self.assertEqual(rows[0]['RESULT'],'PASS');self.assertEqual(rows[0]['TOTAL'],7)
    def test_pass_when_nothing_found(self):
        empty=mart([],[seg('B1','APPROVE','APPROVE')])
        for n in ('check_cutoff_shifted','check_stop_factor_new'):
            self.assertEqual(load(n).run(empty)[0]['RESULT'],'PASS')
    def test_decision_causes(self):
        import reports
        with patch.object(reports,'connect') as c:
            c.return_value.__enter__.return_value=self.m.db
            reports.decision_causes.cache_clear();causes={k:ids for k,ids,_ in reports.decision_causes('x')};reports.decision_causes.cache_clear()
        self.assertEqual(causes,{'Порог':['A1','A3'],'Новый стоп-фактор: DOCS_EXPIRED':['A4'],'Стоп-фактор снят: AGELOW@21':['A5']})

class LocalRunner(unittest.TestCase):
    def test_local_mode_available_and_command(self):
        import sandbox
        old=(sandbox.LOCAL,sandbox.RESOLVED_IMAGE)
        try:
            sandbox.enable_local();ok,msg=sandbox.available()
            self.assertTrue(ok);self.assertEqual(sandbox.RESOLVED_IMAGE,sandbox.LOCAL_IMAGE)
        finally:sandbox.LOCAL,sandbox.RESOLVED_IMAGE=old
    def test_local_mode_requires_loopback(self):
        import subprocess
        with tempfile.TemporaryDirectory() as d:
            r=subprocess.run([sys.executable,str(ROOT/'app.py'),'--local-runner','--host','0.0.0.0'],capture_output=True,text=True,env={'DD_STATE':d,'PATH':''},timeout=30)
        self.assertNotEqual(r.returncode,0);self.assertIn('127.0.0.1',r.stderr)

if __name__=='__main__':unittest.main()


class WindowsFileLocks(unittest.TestCase):
    """On Windows status.json can be briefly locked by the server or OneDrive."""
    def test_dump_retries_locked_target(self):
        import engine
        with tempfile.TemporaryDirectory() as d:
            target=Path(d)/'status.json';calls={'n':0};real=Path.replace
            def flaky(self,other):
                calls['n']+=1
                if calls['n']<3:raise PermissionError(5,'Отказано в доступе')
                return real(self,other)
            with patch.object(Path,'replace',flaky),patch.object(engine.time,'sleep'):
                engine.dump(target,{'ok':1})
            self.assertEqual(json.loads(target.read_text(encoding='utf-8')),{'ok':1});self.assertEqual(calls['n'],3)
    def test_progress_write_failure_does_not_fail_check(self):
        import engine
        m=mart([],[seg('A1','APPROVE','APPROVE')])
        with tempfile.TemporaryDirectory() as d:
            folder=Path(d);(folder/'scripts').mkdir()
            (folder/'scripts'/'c.py').write_text("CHECK={'id':'c','label':'C','entrypoint':lambda mart,p:[{'LABEL':'C','SOURCE':'AFTER','ARGUMENT':'','CNT':0,'TOTAL':1,'METRIC':0,'APP_IDS':[],'RESULT':'PASS','CONFIDENCE':'HIGH'}]}",encoding='utf-8')
            def log(*a,**k):raise PermissionError(5,'locked')
            # log failing after a successful check must not be recorded as a check error
            try:engine.checks(m.db,folder,{'checks':['c.py']},log)
            except PermissionError:pass
            self.assertFalse((folder/'checks.json').exists() and json.loads((folder/'checks.json').read_text(encoding='utf-8'))['errors'])
