import os,tempfile,unittest,sys
from pathlib import Path
from fastapi.testclient import TestClient
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
from main import app
class CampusRadarTests(unittest.TestCase):
 def setUp(self):self.tmp=tempfile.TemporaryDirectory();os.environ['CAMPUSRADAR_DATA_DIR']=self.tmp.name;self.client=TestClient(app)
 def tearDown(self):self.client.close();self.tmp.cleanup();os.environ.pop('CAMPUSRADAR_DATA_DIR',None)
 def test_health(self):self.assertEqual(self.client.get('/api/health').json()['workflow'],'langgraph+langchain')
 def test_profile_and_source(self):
  self.assertEqual(self.client.put('/api/profile',json={'school':'示例大学','major':'软件工程'}).json()['school'],'示例大学')
  source=self.client.post('/api/sources',json={'url':'https://example.edu/notice','label':'教务处'}).json();self.assertEqual(source['label'],'教务处')
 def test_task_update(self):
  from storage import connect,now
  with connect() as db:
   db.execute("INSERT INTO sources(id,kind,url,label) VALUES('s','web','https://example.edu','学校')")
   db.execute("INSERT INTO notices(id,source_id,external_id,title,url,content,first_seen,updated_at) VALUES('n','s','e','奖学金申请','https://example.edu/n','请提交材料',?,?)",(now(),now()))
  self.assertEqual(self.client.put('/api/notices/n/task',json={'status':'todo','notes':'准备成绩单'}).json()['task_status'],'todo')
 def test_action_items_can_be_reordered_and_updated(self):
  from storage import connect,now
  with connect() as db:
   db.execute("INSERT INTO sources(id,kind,url,label) VALUES('s','web','https://example.edu','学校')")
   db.execute("INSERT INTO notices(id,source_id,external_id,title,url,content,first_seen,updated_at) VALUES('n','s','e','奖学金申请','https://example.edu/n','截止 2026-10-20',?,?)",(now(),now()))
  actions=[{'id':'prepare','title':'准备材料','order':2},{'id':'submit','title':'提交申请','order':1,'status':'todo'}]
  updated=self.client.put('/api/notices/n/tasks',json={'action_items':actions})
  self.assertEqual(updated.status_code,200)
  self.assertEqual([x['id'] for x in updated.json()['tasks']],['submit','prepare'])
  single=self.client.put('/api/notices/n/tasks/prepare',json={'status':'done'})
  self.assertEqual(single.json()['tasks'][1]['status'],'done')
  self.assertEqual(self.client.get('/api/notices/n/analysis').json()['tasks'][1]['status'],'done')
 def test_analysis_reads_body_and_preserves_progress(self):
  from storage import connect,now
  from unittest.mock import patch
  with connect() as db:
   db.execute("INSERT INTO notices(id,external_id,title,url,content,first_seen,updated_at) VALUES('n','e','奖学金申请通知','https://example.edu/n','奖学金申请通知',?,?)",(now(),now()))
  body={'content':'申请奖学金，请于2026年10月20日提交成绩单。','links':[]}
  output={'relevant':True,'reason':'符合学生档案','summary':'准备成绩单后提交','deadline':'2026-10-20','materials':['成绩单'],'action_items':[{'id':'a1','title':'准备成绩单'}]}
  with patch('agent.fetch_notice',return_value=body),patch('agent.model_json',return_value=output):
   result=self.client.post('/api/notices/n/analyze')
   self.assertEqual(result.status_code,200)
   self.assertEqual(result.json()['deadline'],'2026-10-20')
   self.assertIn('成绩单',result.json()['content'])
   self.client.put('/api/notices/n/tasks/a1',json={'status':'done'})
   self.client.put('/api/notices/n/analysis',json={'material_done':['成绩单']})
   again=self.client.post('/api/notices/n/analyze').json()
   self.assertEqual(again['tasks'][0]['status'],'done')
   self.assertEqual(again['analysis']['material_done'],['成绩单'])
 def test_generated_deadline_without_evidence_is_discarded(self):
  from agent import verify
  result=verify({'notice':{'content':'本次活动无明确截止日期'},'result':{'deadline':'2026-10-20','action_items':[{'title':'报名','due_date':'2026-10-20'}]}})['verified']
  self.assertEqual(result['deadline'],'')
  self.assertEqual(result['tasks'][0]['due_date'],'')
 def test_body_extraction_excludes_site_navigation(self):
  from providers import extract_notice
  markup='<header>网站导航</header><a href="/home">首页</a><form><div id="vsb_content"><div class="v_news_content"><p>通知正文第一段</p><p>报名截至2026年10月20日。</p><a href="/apply">报名入口</a><script>广告</script></div></div><div class="fjxz"><a href="/file.docx">申请表</a></div><div class="page"><span>上一条</span><a href="/other">其他通知</a></div></form><footer>页脚</footer>'
  detail=extract_notice(markup,'https://example.edu/notice')
  self.assertEqual(detail['content'],'通知正文第一段\n报名截至2026年10月20日。\n报名入口')
  self.assertEqual([x['label'] for x in detail['links']],['报名入口','申请表'])
  self.assertTrue(detail['links'][1]['attachment'])
 def test_category_page_is_not_a_notice_body(self):
  from providers import extract_notice
  with self.assertRaises(ValueError):extract_notice('<div><a href="/exam">考试管理</a><a href="/home">首页</a></div>','https://example.edu')
 def test_webpage_preserves_table_without_scripts(self):
  from providers import extract_notice
  from storage import connect,now
  from unittest.mock import patch
  markup='<article><p>微专业招生目录</p><table><tr><th>学院</th><th>专业</th></tr><tr><td rowspan="2">经济学院</td><td>金融</td></tr></table><img src="/notice.png" onerror="alert(1)"><script>alert(1)</script><a href="javascript:alert(1)">不安全链接</a></article>'
  detail=extract_notice(markup,'https://example.edu/notice')
  self.assertIn('<table>',detail['content_html'])
  self.assertIn('rowspan="2"',detail['content_html'])
  self.assertIn('https://example.edu/notice.png',detail['content_html'])
  self.assertNotIn('onerror',detail['content_html'])
  self.assertNotIn('<script',detail['content_html'])
  self.assertNotIn('javascript:',detail['content_html'])
  with connect() as db:db.execute("INSERT INTO notices(id,external_id,title,url,first_seen,updated_at) VALUES('web','web','招生通知','https://example.edu/notice',?,?)",(now(),now()))
  with patch('agent.fetch_notice',return_value=detail):
   response=self.client.get('/api/notices/web/webpage')
   self.assertEqual(response.status_code,200)
   self.assertIn('text/html',response.headers['content-type'])
   self.assertIn('<table>',response.text)
   self.assertIn("default-src 'none'",response.headers['content-security-policy'])
 def test_download_directory_extracts_only_resources(self):
  from providers import extract_notice
  from unittest.mock import patch
  markup='<nav><a href="/home">首页</a></nav><div class="down-load"><script>bad()</script><ul><li><a href="../info/1068/8173.htm">考试工作管理办法</a></li><li><a href="../info/1068/8168.htm">缓考申请表</a></li></ul><div>首页 上页 共7条</div></div><footer>处长信箱</footer>'
  detail=extract_notice(markup,'https://example.edu/zlxz1/ksgl.htm')
  self.assertEqual(detail['page_type'],'resources')
  self.assertIn('资料下载目录',detail['content'])
  self.assertNotIn('处长信箱',detail['content'])
  self.assertNotIn('共7条',detail['content'])
  self.assertEqual(len(detail['links']),2)
  self.assertIn('https://example.edu/info/1068/8173.htm',detail['links'][0]['url'])
  from storage import connect,now
  with connect() as db:db.execute("INSERT INTO notices(id,external_id,title,url,content,content_version,first_seen,updated_at) VALUES('directory','directory','考试管理','https://example.edu/zlxz1/ksgl.htm','旧缓存','0',?,?)",(now(),now()))
  with patch('agent.fetch_notice',return_value=detail),patch('agent.model_json',return_value={'relevant':True,'summary':'考试资料目录','materials':[],'action_items':[]}):
   result=self.client.post('/api/notices/directory/analyze')
  self.assertEqual(result.status_code,200)
  self.assertEqual(result.json()['content_version'],'2')
  self.assertIn('资料下载目录',result.json()['content'])
 def test_body_failure_exposes_actionable_reason(self):
  from storage import connect,now
  from unittest.mock import patch
  with connect() as db:db.execute("INSERT INTO notices(id,external_id,title,url,first_seen,updated_at) VALUES('bad','bad','通知','https://example.edu/bad',?,?)",(now(),now()))
  with patch('agent.fetch_notice',side_effect=ValueError('该页面是栏目列表，请打开具体通知')):
   result=self.client.post('/api/notices/bad/analyze')
  self.assertEqual(result.status_code,400)
  self.assertIn('栏目列表',result.json()['detail'])
if __name__=='__main__':unittest.main()
