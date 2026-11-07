"""Transport regressions, including actual local chunked HTTP/SSE; no Google key/API."""
import http.client,io,json,os,socket,sys,threading,unittest,urllib.error,urllib.request
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import gemini_client as g
SCHEMA={'type':'object','properties':{'ok':{'type':'boolean'}}}
KEY='fake-unit-secret'
def frame(text='',finish=None,thought=False):
 candidate={'index':0,'content':{'parts':[{'text':text,'thought':thought}]}}
 if finish:candidate['finishReason']=finish
 return {'candidates':[candidate]}
def sse(*chunks):return ''.join('data: '+json.dumps(c,ensure_ascii=False)+'\r\n\r\n' for c in chunks).encode()
class Reply(io.BytesIO):
 def __init__(self,data,content_type='text/event-stream'):
  super().__init__(data);self.status=200;self.headers={'Content-Type':content_type}
class ClientTests(unittest.TestCase):
 def setUp(self):
  self.env=patch.dict(os.environ,{'GEMINI_API_KEY':KEY,'GEMINI_RESPONSE_MODE':'stream','GEMINI_THINKING_LEVEL':'low','GEMINI_TIMEOUT':'90'});self.env.start()
 def tearDown(self):self.env.stop()
 def call(self,reply):
  with patch.object(g.urllib.request,'urlopen',return_value=reply) as send:
   result=g.generate('gemini-3.8-flash','system',[{'id':'E1'}],SCHEMA)
  return result,send
 def test_fragmented_sse_thoughts_and_final_usage(self):
  reply=Reply(b': heartbeat\r\n\r\n'+sse(frame('Do not publish',thought=True),frame('{"ok":'),frame('true,"text":"Привет"}',finish='STOP'),{'usageMetadata':{'totalTokenCount':42}}))
  with self.assertLogs(g.LOG,level='INFO') as logs:result,send=self.call(reply)
  self.assertEqual(result,{'ok':True,'text':'Привет'});self.assertEqual(send.call_count,1)
  request=send.call_args.args[0];body=json.loads(request.data)
  self.assertTrue(request.full_url.endswith(':streamGenerateContent?alt=sse'));self.assertNotIn(KEY,request.full_url);self.assertNotIn(KEY,request.data.decode());self.assertEqual(request.get_header('X-goog-api-key'),KEY)
  self.assertEqual(body['generationConfig']['thinkingConfig']['thinkingLevel'],'low');self.assertNotIn('Привет',' '.join(logs.output));self.assertNotIn(KEY,' '.join(logs.output))
 def test_multiline_events_comments_and_eof_without_blank(self):
  event=json.dumps(frame('{"ok":true}',finish='STOP'),indent=2)
  reply=Reply(('event: message\n'+'\n'.join('data: '+line for line in event.splitlines())).encode())
  result,_=self.call(reply);self.assertEqual(result,{'ok':True})
 def test_model_specific_thinking_defaults(self):
  with patch.dict(os.environ):
   os.environ.pop('GEMINI_THINKING_LEVEL',None)
   with patch.object(g.urllib.request,'urlopen',return_value=Reply(sse(frame('{"ok":true}',finish='STOP')))) as send:
    g.generate('gemini-3.8-flash','system',[],SCHEMA)
   self.assertEqual(json.loads(send.call_args.args[0].data)['generationConfig']['thinkingConfig']['thinkingLevel'],'low')
   with patch.object(g.urllib.request,'urlopen',return_value=Reply(sse(frame('{"ok":true}',finish='STOP')))) as send:
    g.generate('gemini-2.5-flash','system',[],SCHEMA)
   self.assertNotIn('thinkingConfig',json.loads(send.call_args.args[0].data)['generationConfig'])
 def test_single_mode_compatibility(self):
  with patch.dict(os.environ,{'GEMINI_RESPONSE_MODE':'single'}):result,send=self.call(Reply(json.dumps(frame('{"ok":true}',finish='STOP')).encode(),'application/json'))
  self.assertTrue(result['ok']);self.assertTrue(send.call_args.args[0].full_url.endswith(':generateContent'))
 def test_eof_or_done_without_stop_is_not_a_success(self):
  for body in [sse(frame('{"ok":true}')),sse(frame('{"ok":true}'))+b'data: [DONE]\n\n']:
   with self.subTest(body=body),self.assertRaisesRegex(g.GeminiError,'без подтверждения'):self.call(Reply(body))
 def test_truncation_invalid_json_and_block(self):
  cases=[(sse(frame('{"ok":',finish='MAX_TOKENS')),'усечён'),(sse(frame('{broken}',finish='STOP')),'некорректный JSON'),(b'data: {broken}\n\n','Повреждённое'),(sse({'promptFeedback':{'blockReason':'SAFETY'}}),'заблокировал')]
  for body,pattern in cases:
   with self.subTest(pattern=pattern),self.assertRaisesRegex(g.GeminiError,pattern):self.call(Reply(body))
 def test_disconnect_before_headers_no_retry_and_stage(self):
  with patch.object(g.urllib.request,'urlopen',side_effect=http.client.RemoteDisconnected('upstream closed '+KEY)) as send,self.assertLogs(g.LOG,level='WARNING') as logs:
   with self.assertRaisesRegex(g.GeminiError,'без HTTP-ответа') as caught:g.generate('gemini-3.8-flash','system',[],SCHEMA)
  self.assertEqual(send.call_count,1);self.assertIn('ожидание HTTP-заголовков',str(caught.exception));self.assertNotIn(KEY,str(caught.exception)+' '.join(logs.output))
 def test_incomplete_http_after_headers_is_not_published(self):
  class Interrupted(Reply):
   def readline(self,n):raise http.client.IncompleteRead(b'data: {"secret":',100)
  with self.assertRaisesRegex(g.GeminiError,'часть HTTP-ответа') as caught:self.call(Interrupted(b''))
  self.assertIn('чтение ответа',str(caught.exception));self.assertNotIn('secret',str(caught.exception))
 def test_timeouts_dns_and_tls_are_distinct(self):
  import ssl
  for ex,pattern in [(socket.timeout(),'время ожидания'),(urllib.error.URLError(socket.gaierror(-2,'DNS')),'DNS'),(urllib.error.URLError(ssl.SSLError('certificate')),'TLS')]:
   with self.subTest(pattern=pattern),patch.object(g.urllib.request,'urlopen',side_effect=ex),self.assertRaisesRegex(g.GeminiError,pattern):g.generate('gemini-3.8-flash','system',[],SCHEMA)
 def test_api_error_details_redact_credentials(self):
  raw=json.dumps({'error':{'status':'INVALID_ARGUMENT','message':'Key '+KEY+'; https://user:password@proxy.test; bad parameter'}}).encode()
  error=urllib.error.HTTPError('https://invalid/?key='+KEY,400,'bad',{},io.BytesIO(raw))
  with patch.object(g.urllib.request,'urlopen',side_effect=error),self.assertRaises(g.GeminiError) as caught:g.generate('gemini-3.8-flash','system',[],SCHEMA)
  message=str(caught.exception);self.assertIn('INVALID_ARGUMENT',message);self.assertIn('bad parameter',message);self.assertNotIn(KEY,message);self.assertNotIn('password',message)
 def test_stream_limits_and_setting_errors_before_request(self):
  with self.assertRaisesRegex(g.GeminiError,'длинная строка'):self.call(Reply(b'data: '+b'x'*(g.MAX_EVENT+1)))
  with patch.object(g,'MAX_BYTES',100),self.assertRaisesRegex(g.GeminiError,'2 МБ'):self.call(Reply(sse(frame('x'*100))))
  with patch.dict(os.environ,{'GEMINI_TIMEOUT':'not-a-number'}),patch.object(g.urllib.request,'urlopen') as send,self.assertRaisesRegex(g.GeminiError,'GEMINI_TIMEOUT'):g.generate('gemini-3.8-flash','system',[],SCHEMA)
  send.assert_not_called()
 def test_real_chunked_http_response_and_abrupt_disconnect(self):
  payload=sse(frame('{"ok":'),frame('true,"text":"тест"}',finish='STOP'))
  records=[]
  class Handler(BaseHTTPRequestHandler):
   protocol_version='HTTP/1.1'
   def log_message(self,*args):pass
   def do_POST(self):
    raw=self.rfile.read(int(self.headers['Content-Length']));records.append((self.path,json.loads(raw),self.headers.get('x-goog-api-key')))
    if self.path=='/drop':self.close_connection=True;return
    self.send_response(200);self.send_header('Content-Type','text/event-stream');self.send_header('Transfer-Encoding','chunked');self.end_headers()
    # Cut inside UTF-8 and SSE tokens: http.client must reconstruct bytes correctly.
    for i in range(0,len(payload),7):
     chunk=payload[i:i+7];self.wfile.write(('%x\r\n'%len(chunk)).encode()+chunk+b'\r\n');self.wfile.flush()
    self.wfile.write(b'0\r\n\r\n');self.wfile.flush()
  server=ThreadingHTTPServer(('127.0.0.1',0),Handler);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start();real_open=urllib.request.urlopen
  base='http://127.0.0.1:'+str(server.server_port)
  def local(path):
   def open_local(request,timeout):return real_open(urllib.request.Request(base+path,data=request.data,headers=dict(request.header_items())),timeout=timeout)
   return open_local
  try:
   with patch.object(g.urllib.request,'urlopen',side_effect=local('/stream')):result=g.generate('gemini-3.8-flash','system',[],SCHEMA)
   self.assertEqual(result,{'ok':True,'text':'тест'});self.assertEqual(records[0][2],KEY)
   with patch.object(g.urllib.request,'urlopen',side_effect=local('/drop')),self.assertRaisesRegex(g.GeminiError,'без HTTP-ответа'):g.generate('gemini-3.8-flash','system',[],SCHEMA)
   self.assertEqual(len(records),2) # Exactly one request per call, no automatic retry.
  finally:server.shutdown();server.server_close();thread.join()
if __name__=='__main__':unittest.main()
