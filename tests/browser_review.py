"""Optional Playwright UI pass using an isolated fixture and a FAKE Gemini transport.
Requires npm package playwright and its Chromium (or DD_BROWSER_EXECUTABLE).
No real user account or API is used. Screenshots stay in a temporary directory.
"""
import json,os,subprocess,sys,tempfile
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from test_review_features import ReviewTests
import ai_review
os.environ['GEMINI_API_KEY']='qa-fake-key';os.environ['GEMINI_MODEL']='qa-model'
ai_review.transport=lambda model,payload:{'summary':'Тестовый ответ транспорта: несколько изменений требуют проверки.','findings':[{'severity':'medium','observation':'Есть различия','hypothesis':'Возможна смена правила','recommendation':'Открыть выбранную заявку','evidence_ids':['E1']}],'limitations':['Тест интерфейса с подменой HTTP-транспорта']}
test=ReviewTests();test.setUp()
try:
 eid,rid,check,_=test.fixture();os.environ['DD_QA_URL']=test.base;os.environ['DD_QA_EID']=eid
 with tempfile.TemporaryDirectory() as screenshots:
  os.environ['DD_QA_SCREENSHOTS']=screenshots
  result=subprocess.run(['node',str(Path(__file__).with_suffix('.cjs'))]);sys.exit(result.returncode)
finally:test.tearDown()
