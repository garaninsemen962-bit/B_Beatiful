import copy, importlib.util, json, sqlite3, sys, unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from engine import Mart, COLUMNS, load
norm=load(ROOT/'procedures/normalize.py');compare=load(ROOT/'procedures/compare.py')
def diff(b,a,params=None):
    return list(compare.compare(norm.normalize(b,'x','BEFORE',params or {})+norm.normalize(a,'x','AFTER',params or {}),{}))
class ComparisonTests(unittest.TestCase):
    def test_technical_fields_ignored(self):
        self.assertEqual(diff({'appData':{'request_id':'a'}},{'appData':{'request_id':'b'}}),[])
    def test_array_order_and_nested_keys(self):
        b={'decisionResult':{'product_trials':[{'product_id':1,'rules':[{'name':'A','hit':True},{'name':'B','hit':False}]},{'product_id':2,'rules':[{'name':'A','hit':False}]}]}}
        a=copy.deepcopy(b);a['decisionResult']['product_trials'].reverse();a['decisionResult']['product_trials'][1]['rules'].reverse()
        self.assertTrue(all(r['DIFF_FLAG']==0 for r in diff(b,a)))
        rows=diff(b,a);self.assertEqual(len(rows),len({(r['PATH'],r['ARRAY_KEY']) for r in rows}))
    def test_composite_offer_key(self):
        doc={'decisionResult':{'available_offers':[{'product_id':1,'type':'standard'},{'product_id':1,'type':'manual'}]}}
        self.assertEqual(len(diff(doc,doc)),4)
    def test_types_and_missing(self):
        rows={r['KEY']:r for r in diff({'a':None,'b':False,'c':0,'d':'','gone':1},{'a':'NULL','b':0,'c':'0','d':None,'new':None})}
        self.assertTrue(all(r['DIFF_FLAG'] for r in rows.values()))
        self.assertEqual(rows['gone']['CHANGE_TYPE'],'ATTRIBUTE_LOST');self.assertEqual(rows['new']['CHANGE_TYPE'],'ATTRIBUTE_NEW')
    def test_value_change_inside_bucket(self):
        r=diff({'score':.42},{'score':.58})[0]
        self.assertEqual(r['ADJUSTED_BEFORE'],r['ADJUSTED_AFTER']);self.assertEqual(r['DIFF_FLAG'],1)
    def test_index_vector(self):
        params={'arrays':{'vector':'index'}}
        self.assertEqual(sum(r['DIFF_FLAG'] for r in diff({'vector':[.1,.2]},{'vector':[.2,.1]},params)),2)
    def test_unknown_array_and_duplicate_fail(self):
        with self.assertRaises(ValueError):norm.normalize({'x':[1]},'x','BEFORE',{})
        with self.assertRaises(ValueError):norm.normalize({'decisionResult':{'scoring':[{'modelName':'a'},{'modelName':'a'}]}},'x','BEFORE',{})
class MartTests(unittest.TestCase):
    def setUp(self):
        self.db=sqlite3.connect(':memory:');self.db.execute('CREATE TABLE comparison('+','.join(c+(' INTEGER' if c=='DIFF_FLAG' else ' TEXT') for c in COLUMNS)+')')
        rows=diff({'a':1,'b':2},{'a':3,'c':4})
        self.db.executemany('INSERT INTO comparison VALUES ('+','.join('?' for _ in COLUMNS)+')',[[r[c] for c in COLUMNS] for r in rows]);self.m=Mart(self.db)
    def test_filters(self):
        self.assertEqual(self.m.count(change_type=['ATTRIBUTE_NEW','ATTRIBUTE_LOST']),2)
        self.assertEqual(self.m.app_ids(path='a',diff_flag=1),['x']);self.assertEqual(self.m.count(app_ids=[]),0)
        self.assertEqual(self.m.count(path_prefix='a'),1);self.assertEqual(self.m.count(value_before='1'),1)
    def tearDown(self):self.db.close()
if __name__=='__main__':unittest.main()
