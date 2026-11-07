"""Optional Playwright UI pass using an isolated fixture and a FAKE Gemini transport.
Requires npm package playwright and its Chromium (or DD_BROWSER_EXECUTABLE).
No real user account or API is used. Screenshots stay in a temporary directory.
"""
import json,os,subprocess,sys,tempfile,time,shutil
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from test_review_features import ReviewTests,detailed_response
import ai_review,ai_models
os.environ['GEMINI_API_KEY']='qa-fake-key';os.environ['GEMINI_MODEL']='qa-model'
def fake_transport(model,payload):
 time.sleep(3)
 return detailed_response('Тестовый подробный ответ модели '+model)
ai_review.transport=fake_transport
ai_models.catalog=lambda:[{'id':'gemini-3.5-flash-lite','label':'Gemini 3.5 Flash-Lite'},{'id':'gemini-3.8-flash','label':'Gemini 3.8 Flash'}]

test=ReviewTests();test.setUp()
try:
 eid,rid,check,_=test.fixture();os.environ['DD_QA_URL']=test.base;os.environ['DD_QA_EID']=eid
 with tempfile.TemporaryDirectory() as screenshots:
  os.environ['DD_QA_SCREENSHOTS']=screenshots
  result=subprocess.run(['node',str(Path(__file__).with_suffix('.cjs'))])
  if os.environ.get('DD_QA_SCREENSHOTS_KEEP'):shutil.copytree(screenshots,os.environ['DD_QA_SCREENSHOTS_KEEP'],dirs_exist_ok=True)
  sys.exit(result.returncode)
finally:test.tearDown()
