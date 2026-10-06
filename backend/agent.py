from __future__ import annotations
import json,re,threading,uuid
from typing import TypedDict
import httpx
from langgraph.graph import END,StateGraph
try:
 from langchain_core.output_parsers import JsonOutputParser
 from langchain_text_splitters import RecursiveCharacterTextSplitter
except ImportError:
 class JsonOutputParser:
  def parse(self,text): return json.loads(text)
 class RecursiveCharacterTextSplitter:
  def __init__(self,chunk_size=5000,chunk_overlap=300):self.chunk_size=chunk_size;self.chunk_overlap=chunk_overlap
  def split_text(self,text):return [text[i:i+self.chunk_size] for i in range(0,len(text),self.chunk_size-self.chunk_overlap)]
from providers import fetch_source,fetch_notice
from storage import connect,event,get_notice,get_profile,get_settings,now
LOCK=threading.Lock(); splitter=RecursiveCharacterTextSplitter(chunk_size=5000,chunk_overlap=300); parser=JsonOutputParser()
def parse_model_json(content):
 text=str(content or '').strip()
 text=re.sub(r'^```(?:json)?\s*|\s*```$','',text,flags=re.I).strip()
 try:return parser.parse(text)
 except Exception:
  match=re.search(r'\{.*\}',text,re.S)
  if match:
   try:return json.loads(match.group(0))
   except Exception:pass
  raise ValueError('模型返回的内容不是有效 JSON，请重试或更换模型')
def model_json(system,user):
 with connect() as db:cfg=get_settings(db)
 provider=cfg['provider'];key=None
 if provider not in {'openai','deepseek'}:raise ValueError('请先在设置中选择模型并保存 API Key')
 try:
  import keyring;key=keyring.get_password('CampusRadar',provider)
 except Exception:pass
 if not key:
  with connect() as db:key=str(db.execute(f'SELECT {provider}_key FROM settings WHERE id=1').fetchone()[0] or '')
 if provider not in {'openai','deepseek'} or not key:raise ValueError('请先在设置中选择模型并保存 API Key')
 official=provider=='openai'
 endpoint=('https://api.openai.com/v1/responses' if official else str(cfg.get(provider+'_base_url') or 'https://api.deepseek.com').rstrip('/')+'/chat/completions')
 request={'model':cfg[provider+'_model'],'messages':[{'role':'system','content':system},{'role':'user','content':json.dumps(user,ensure_ascii=False)}]}
 if official:
  request={'model':cfg[provider+'_model'],'instructions':system+'\n只返回 JSON，不要 Markdown。','input':json.dumps(user,ensure_ascii=False),'reasoning':{'effort':'medium'},'max_output_tokens':6000}
 else:
  request.update({'temperature':0,'response_format':{'type':'json_object'}})
 try:
  with httpx.Client(timeout=90) as client:r=client.post(endpoint,headers={'Authorization':'Bearer '+key},json=request)
  r.raise_for_status()
 except httpx.HTTPStatusError as exc:
  if exc.response.status_code in {401,403}:raise ValueError('API Key 无效或没有该模型的访问权限') from exc
  if exc.response.status_code == 404:raise ValueError('模型名称不存在，请在设置中点击“读取模型”并重新选择') from exc
  detail=re.sub(r'\s+',' ',exc.response.text or '')[:240]
  raise ValueError(f'模型服务返回错误（HTTP {exc.response.status_code}）：{detail or "请检查模型名称和网络连接"}') from exc
 except httpx.RequestError as exc:raise ValueError('无法连接模型服务，请检查网络连接') from exc
 try:
  payload=r.json()
  if official:
   content=payload.get('output_text','')
   if not content:
    content=''.join(str(part.get('text','')) for item in payload.get('output',[]) for part in item.get('content',[]) if isinstance(part,dict) and part.get('type')=='output_text')
  else: content=payload['choices'][0]['message']['content']
 except Exception as exc:raise ValueError('模型接口返回格式异常，请检查模型名称或网络连接') from exc
 return parse_model_json(content)
class NoticeState(TypedDict,total=False):notice_id:str;notice:dict;profile:dict;result:dict;verified:dict
def refresh_notice_body(nid):
 with connect() as db:n=get_notice(db,nid)
 if not n:raise ValueError('通知不存在')
 detail=fetch_notice(n['url'])
 analysis=dict(n.get('analysis') or {});analysis['links']=detail['links']
 with connect() as db:
  db.execute('UPDATE notices SET content=?,content_html=?,notice_links_json=?,content_version=?,analysis_json=?,updated_at=? WHERE id=?',(detail['content'],detail.get('content_html',''),json.dumps(detail['links'],ensure_ascii=False),'2',json.dumps(analysis,ensure_ascii=False),now(),nid))
  return get_notice(db,nid)
def load_notice(s):
 with connect() as db:n=get_notice(db,s['notice_id']);p=get_profile(db)
 if not n:raise ValueError('通知不存在')
 if int(n.get('content_version') or 0)<2 or not n['content'].strip():
  try:n=refresh_notice_body(n['id'])
  except Exception as exc:raise ValueError('无法读取通知正文，请检查原通知链接后重试') from exc
 n['source_links']=n.get('notice_links',[])
 return {'notice':n,'profile':p}
def assess(s):
 system='''你是校园事务助手。根据学生档案与通知原文，整理成可以直接执行的办理计划。只依据通知原文，不能猜测；不确定的字段返回空字符串或空数组。只返回一个 JSON 对象，不要 Markdown。
字段要求：
relevant(boolean)：是否与学生档案有关；reason(string)：一句话说明依据；summary(string)：给学生的简短结论；
category(string)：exam/scholarship/teaching/activity/competition/administration/other 之一；priority(string)：urgent/high/normal/low 之一；
deadline(string)：通知明确写出的截止日期，统一 YYYY-MM-DD，无法确定则空字符串；deadline_text(string)：原文中的日期或时间表达；
materials(string数组)：需要准备的材料名称；material_details(对象数组)：每项包含 name、required(boolean)、note；
action_items(对象数组)：按办理顺序列出 1-8 个可执行步骤，每项包含 id、title、detail、due_date(YYYY-MM-DD或空)、status(new/todo/doing/done)、order(整数)、depends_on(字符串数组)；
links(对象数组)：通知中明确出现的链接，每项包含 label、url；contacts(对象数组)：通知中明确出现的联系人或联系方式，每项包含 label、value；
risks(string数组)：容易错过的要求或风险。没有信息时返回空数组。'''
 return {'result':model_json(system,{'student':s['profile'],'notice':{'title':s['notice']['title'],'url':s['notice']['url'],'links':s['notice'].get('source_links',[]),'content':'\n'.join(splitter.split_text(s['notice']['content'])[:4])}})}
def verify(s):
 raw=s['result'] if isinstance(s['result'],dict) else {};text=s['notice']['content']
 deadline=str(raw.get('deadline') or '')[:20]
 # 日期必须同时出现在通知原文中，避免模型凭空生成截止日期。
 def evidenced_date(value):
  from datetime import date
  try:
   d=date.fromisoformat(str(value));patterns=[d.isoformat(),f'{d.year}年{d.month}月{d.day}日',f'{d.year}年{d.month:02}月{d.day:02}日',f'{d.year}/{d.month}/{d.day}',f'{d.year}.{d.month}.{d.day}']
   return d.isoformat() if any(p in text for p in patterns) else ''
  except (ValueError,TypeError):return ''
 deadline=evidenced_date(deadline)
 categories={'exam','scholarship','teaching','activity','competition','administration','other'}
 priorities={'urgent','high','normal','low'}
 statuses={'new','todo','doing','done'}
 category=str(raw.get('category') or 'other').lower();category=category if category in categories else 'other'
 priority=str(raw.get('priority') or 'normal').lower();priority=priority if priority in priorities else 'normal'
 def clean_date(value):
  value=str(value or '')[:20]
  return evidenced_date(value)
 materials=[]
 for item in raw.get('materials',[]):
  if isinstance(item,(str,int,float)) and str(item).strip():materials.append(str(item).strip()[:200])
 details=[]
 for item in raw.get('material_details',[]):
  if isinstance(item,dict) and str(item.get('name') or '').strip():
   details.append({'name':str(item.get('name')).strip()[:200],'required':bool(item.get('required',True)),'note':str(item.get('note') or '').strip()[:500]})
 actions=[]
 for index,item in enumerate(raw.get('action_items',[])[:12],1):
  if not isinstance(item,dict) or not str(item.get('title') or '').strip():continue
  status=str(item.get('status') or 'new').lower();status=status if status in statuses else 'new'
  deps=item.get('depends_on',[]);deps=[str(x)[:40] for x in deps if isinstance(x,(str,int))][:6] if isinstance(deps,list) else []
  try:order=int(item.get('order') or index)
  except (TypeError,ValueError):order=index
  actions.append({'id':str(item.get('id') or f'action-{index}')[:60],'title':str(item.get('title')).strip()[:200],'detail':str(item.get('detail') or '').strip()[:1000],'due_date':clean_date(item.get('due_date')),'status':status,'order':order,'depends_on':deps})
 actions.sort(key=lambda x:(x['order'],x['id']))
 links=list(s['notice'].get('source_links',[]))
 contacts=[]
 for item in raw.get('contacts',[]):
  if isinstance(item,dict) and str(item.get('value') or '').strip():contacts.append({'label':str(item.get('label') or '联系人')[:120],'value':str(item.get('value'))[:300]})
 analysis={'summary':str(raw.get('summary') or '').strip()[:1000],'category':category,'priority':priority,'deadline_text':str(raw.get('deadline_text') or '').strip()[:200],'material_details':details,'action_items':actions,'links':links[:12],'contacts':contacts[:12],'risks':[str(x)[:300] for x in raw.get('risks',[]) if isinstance(x,(str,int))][:12]}
 return {'verified':{'relevant':bool(raw.get('relevant')),'reason':str(raw.get('reason') or '')[:1000],'deadline':deadline,'materials':materials[:12],'analysis':analysis,'tasks':actions}}
def save_notice(s):
 v=s['verified']
 old={x['title']:x for x in s['notice'].get('tasks',[])}
 for item in v['tasks']:
  if item['title'] in old:item['status']=old[item['title']]['status'];item['id']=old[item['title']]['id']
 v['analysis']['action_items']=v['tasks']
 v['analysis']['material_done']=s['notice'].get('analysis',{}).get('material_done',[])
 with connect() as db:
  db.execute('UPDATE notices SET relevant=?,relevance_reason=?,deadline=?,materials_json=?,analysis_json=?,tasks_json=?,updated_at=? WHERE id=?',(int(v['relevant']),v['reason'],v['deadline'],json.dumps(v['materials'],ensure_ascii=False),json.dumps(v['analysis'],ensure_ascii=False),json.dumps(v['tasks'],ensure_ascii=False),now(),s['notice_id']))
  event(db,s['notice_id'],'analyzed','已完成相关性、材料和办理计划分析')
 return {}
b=StateGraph(NoticeState)
for n,f in [('load',load_notice),('assess',assess),('verify',verify),('save',save_notice)]:b.add_node(n,f)
b.set_entry_point('load');b.add_edge('load','assess');b.add_edge('assess','verify');b.add_edge('verify','save');b.add_edge('save',END);notice_graph=b.compile()
def analyze_notice(nid):
 notice_graph.invoke({'notice_id':nid})
 with connect() as db:return get_notice(db,nid)
class ScanState(TypedDict,total=False):source_id:str;source:dict;items:list;new_ids:list;analyzed:int
def fetch_node(s):
 with connect() as db:source=dict(db.execute('SELECT * FROM sources WHERE id=?',(s['source_id'],)).fetchone() or {})
 if not source:raise ValueError('来源不存在')
 return {'source':source,'items':fetch_source(source)}
def persist_node(s):
 new=[];stamp=now()
 with connect() as db:
  for item in s['items']:
   old=db.execute('SELECT id FROM notices WHERE source_id=? AND external_id=?',(s['source_id'],item['external_id'])).fetchone()
   if old:db.execute('UPDATE notices SET title=?,url=?,updated_at=?,is_open=1 WHERE id=?',(item['title'],item['url'],stamp,old['id']))
   else:
    nid=uuid.uuid4().hex;db.execute('INSERT INTO notices(id,source_id,external_id,title,url,content,published_at,first_seen,updated_at) VALUES(?,?,?,?,?,?,?,?,?)',(nid,s['source_id'],item['external_id'],item['title'],item['url'],item['content'],item.get('published_at'),stamp,stamp));event(db,nid,'discovered','发现新的校园通知');new.append(nid)
  db.execute('UPDATE sources SET last_sync=?,last_error=? WHERE id=?',(stamp,'',s['source_id']))
 return {'new_ids':new}
def analyze_new(s):
 count=0
 for nid in s['new_ids'][:3]:
  try:analyze_notice(nid);count+=1
  except Exception as exc:
   with connect() as db:event(db,nid,'analysis_failed',str(exc)[:300])
 return {'analyzed':count}
sb=StateGraph(ScanState)
for n,f in [('fetch',fetch_node),('persist',persist_node),('analyze',analyze_new)]:sb.add_node(n,f)
sb.set_entry_point('fetch');sb.add_edge('fetch','persist');sb.add_edge('persist','analyze');sb.add_edge('analyze',END);scan_graph=sb.compile()
def scan_source(sid):
 state=scan_graph.invoke({'source_id':sid});return {'source_id':sid,'fetched':len(state['items']),'new':len(state['new_ids']),'analyzed':state['analyzed'],'error':''}
def scan_all():
 if not LOCK.acquire(False):raise ValueError('通知扫描正在运行，请稍后重试')
 try:
  with connect() as db:ids=[x['id'] for x in db.execute('SELECT id FROM sources WHERE enabled=1')]
  result=[]
  for sid in ids:
   try:result.append(scan_source(sid))
   except Exception as exc:
    with connect() as db:db.execute('UPDATE sources SET last_error=? WHERE id=?',(str(exc)[:500],sid))
    result.append({'source_id':sid,'fetched':0,'new':0,'analyzed':0,'error':str(exc)[:500]})
  return result
 finally:LOCK.release()
