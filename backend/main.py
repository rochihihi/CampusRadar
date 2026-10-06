from __future__ import annotations
import json
import uuid
import html
from fastapi import FastAPI,HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel,Field
from agent import analyze_notice,scan_all,refresh_notice_body
from storage import connect,event,get_notice,get_profile,get_settings,list_notices,now
app=FastAPI(title='CampusRadar 校务雷达',version='0.1.0')
class RagQuestionIn(BaseModel):
 question:str=Field(min_length=2,max_length=500)
 source_id:str=Field(default='',max_length=100)
@app.get('/api/rag/index')
def rag_status():
 from rag import sync_index
 from embeddings import vector_status
 return {**sync_index(),**vector_status()}
class EmbeddingSettingsIn(BaseModel):
 enabled:bool=False
 base_url:str=Field(default='https://api.openai.com/v1',max_length=1000)
 model:str=Field(default='text-embedding-3-small',max_length=200)
 api_key:str|None=Field(default=None,max_length=1000)
 use_openai_key:bool=True
 threshold:float=Field(default=0.35,ge=0,le=1)
@app.get('/api/embeddings/settings')
def embedding_settings():
 from embeddings import config
 return config(public=True)
@app.put('/api/embeddings/settings')
def embedding_save(body:EmbeddingSettingsIn):
 from embeddings import save_config
 try:return save_config(body.model_dump())
 except ValueError as exc:raise HTTPException(400,str(exc)) from exc
@app.post('/api/embeddings/test')
def embedding_test():
 from embeddings import config,embed
 try:
  vector=embed(['校园通知连接测试'],config())[0]
  return {'ok':True,'dimensions':len(vector)}
 except ValueError as exc:raise HTTPException(400,str(exc)) from exc
@app.post('/api/embeddings/build')
def embedding_build():
 from rag import sync_index
 from embeddings import build_batch
 try:
  sync_index()
  return build_batch()
 except ValueError as exc:raise HTTPException(400,str(exc)) from exc
@app.delete('/api/embeddings/cache')
def embedding_clear():
 from embeddings import schema,BUILD_LOCK
 if not BUILD_LOCK.acquire(False):raise HTTPException(409,'向量建库正在执行')
 try:
  with connect() as db:
   schema(db);db.execute('DELETE FROM rag_vectors')
 finally:BUILD_LOCK.release()
 return {'ok':True}
@app.post('/api/rag/ask')
def rag_ask(body:RagQuestionIn):
 from rag import ask
 question=body.question.strip()
 if len(question)<2:raise HTTPException(400,'请输入具体问题')
 try:return ask(question,body.source_id)
 except ValueError as exc:raise HTTPException(400,str(exc)) from exc
 except Exception as exc:raise HTTPException(502,'检索问答失败，请检查模型设置后重试') from exc
class ProfileIn(BaseModel): name:str=''; school:str=''; major:str=''; keywords:str=''
class SourceIn(BaseModel): url:str=Field(min_length=10,max_length=1000); label:str=Field(default='',max_length=120)
class SettingsIn(BaseModel): provider:str; openai_model:str='gpt-6-luna'; deepseek_model:str='deepseek-chat'; openai_key:str|None=None; deepseek_key:str|None=None; openai_base_url:str='https://api.openai.com/v1'; deepseek_base_url:str='https://api.deepseek.com'
class ModelsIn(BaseModel): provider:str; api_key:str|None=None
class TaskIn(BaseModel): status:str; notes:str=''
class AnalysisUpdateIn(BaseModel):
 category:str|None=None
 priority:str|None=None
 summary:str|None=None
 deadline:str|None=None
 relevant:bool|None=None
 materials:list[str]|None=None
 action_items:list[dict]|None=None
 material_done:list[str]|None=None
 tasks:list[dict]|None=None
class ActionsIn(BaseModel):
 action_items:list[dict]|None=None
 tasks:list[dict]|None=None
@app.get('/api/health')
def health(): return {'ok':True,'workflow':'langgraph+langchain'}
@app.get('/api/profile')
def profile():
 with connect() as db:return get_profile(db)
@app.put('/api/profile')
def update_profile(body:ProfileIn):
 with connect() as db:
  db.execute('UPDATE profile SET name=?,school=?,major=?,keywords=?,updated_at=? WHERE id=1',(body.name.strip(),body.school.strip(),body.major.strip(),body.keywords.strip(),now()));return get_profile(db)
@app.get('/api/settings')
def settings():
 with connect() as db:x=get_settings(db)
 for p in ('openai','deepseek'):
  try:
   import keyring;x['has_'+p+'_key']=bool(keyring.get_password('CampusRadar',p) or x.get(p+'_key'))
  except Exception:x['has_'+p+'_key']=False
 x.pop('openai_key',None);x.pop('deepseek_key',None)
 return x
@app.put('/api/settings')
def update_settings(body:SettingsIn):
 if body.provider not in {'none','openai','deepseek'}:raise HTTPException(400,'模型服务无效')
 with connect() as db:
  db.execute('UPDATE settings SET provider=?,openai_model=?,deepseek_model=?,openai_base_url=?,deepseek_base_url=? WHERE id=1',(body.provider,body.openai_model.strip() or 'gpt-6-luna',body.deepseek_model.strip() or 'deepseek-chat',body.openai_base_url.strip().rstrip('/') or 'https://api.openai.com/v1',body.deepseek_base_url.strip().rstrip('/') or 'https://api.deepseek.com'))
 for p,key in (('openai',body.openai_key),('deepseek',body.deepseek_key)):
  if key:
   with connect() as db:db.execute(f'UPDATE settings SET {p}_key=? WHERE id=1',(key.strip(),))
   try:
    import keyring;keyring.set_password('CampusRadar',p,key)
   except Exception:pass
 return settings()
@app.post('/api/models')
def models(body:ModelsIn):
 if body.provider not in {'openai','deepseek'}:raise HTTPException(400,'请选择 OpenAI 或 DeepSeek')
 key=body.api_key.strip() if body.api_key else ''
 if not key:
  try:
   import keyring;key=keyring.get_password('CampusRadar',body.provider) or ''
  except Exception: key=''
 if not key:
  with connect() as db:key=str(db.execute(f'SELECT {body.provider}_key FROM settings WHERE id=1').fetchone()[0] or '')
 if not key:raise HTTPException(400,'请先填写并保存对应 API Key')
 with connect() as db:
  base='https://api.openai.com/v1' if body.provider=='openai' else str(db.execute(f'SELECT {body.provider}_base_url FROM settings WHERE id=1').fetchone()[0] or '')
 endpoint=base.rstrip('/')+'/models'
 try:
  import httpx
  with httpx.Client(timeout=30) as client:r=client.get(endpoint,headers={'Authorization':'Bearer '+key})
  r.raise_for_status();data=r.json().get('data',[])
  ids=sorted({str(x.get('id','')).strip() for x in data if isinstance(x,dict) and x.get('id')})
  if not ids:raise ValueError('接口没有返回可用模型')
  return {'provider':body.provider,'models':ids}
 except Exception as exc:raise HTTPException(502,'读取模型失败，请检查 API Key 和网络连接') from exc
@app.get('/api/sources')
def sources():
 with connect() as db:return [dict(x) for x in db.execute('SELECT * FROM sources ORDER BY label')]
@app.post('/api/sources')
def add_source(body:SourceIn):
 sid=uuid.uuid4().hex
 with connect() as db:
  try:db.execute('INSERT INTO sources(id,kind,url,label) VALUES(?,?,?,?)',(sid,'web',body.url.strip(),body.label.strip() or body.url.split('/')[2]))
  except Exception as exc:raise HTTPException(409,'这个来源已经添加') from exc
  return dict(db.execute('SELECT * FROM sources WHERE id=?',(sid,)).fetchone())
@app.delete('/api/sources/{sid}')
def delete_source(sid):
 with connect() as db:
  if not db.execute('SELECT 1 FROM sources WHERE id=?',(sid,)).fetchone():raise HTTPException(404,'来源不存在')
  db.execute('DELETE FROM sources WHERE id=?',(sid,))
 return {'ok':True}
@app.post('/api/sources/scan')
def scan():
 try:return {'results':scan_all()}
 except ValueError as exc:raise HTTPException(409,str(exc)) from exc
@app.get('/api/notices')
def notices():
 with connect() as db:return list_notices(db)
@app.get('/api/notices/{nid}')
def notice(nid):
 with connect() as db:n=get_notice(db,nid)
 if not n:raise HTTPException(404,'通知不存在')
 return n
@app.post('/api/notices/{nid}/analyze')
def analyze(nid):
 try:return analyze_notice(nid)
 except ValueError as exc:raise HTTPException(400,str(exc)) from exc
 except Exception as exc:raise HTTPException(502,'模型请求失败，请检查 API Key、模型名称和网络连接') from exc
@app.post('/api/notices/{nid}/body')
def refresh_body(nid):
 try:return refresh_notice_body(nid)
 except ValueError as exc:raise HTTPException(400,str(exc)) from exc
 except Exception as exc:raise HTTPException(502,'通知正文读取失败，请检查原通知链接和网络') from exc
@app.get('/api/notices/{nid}/webpage',response_class=HTMLResponse)
def notice_webpage(nid):
 with connect() as db:n=get_notice(db,nid)
 if not n:raise HTTPException(404,'通知不存在')
 try:
  if not n.get('content_html'):n=refresh_notice_body(nid)
  content=n['content_html']
 except Exception as exc:content='<p class="error">'+html.escape(str(exc) if isinstance(exc,ValueError) else '网页读取失败，请重新读取或打开完整网页')+'</p>'
 title=html.escape(n['title'])
 markup='<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>'+title+'</title><style>html{background:#fff;color:#222;font:15px/1.9 "Microsoft YaHei",sans-serif}body{margin:0;padding:24px;overflow-wrap:anywhere}h1{font-size:22px;line-height:1.5;margin:0 0 24px}p{margin:12px 0}table{border-collapse:collapse;width:100%;margin:18px 0;font-size:14px}th,td{border:1px solid #ccc;padding:8px;vertical-align:middle}th{background:#f3f5f8}img{max-width:100%;height:auto}a{color:#225ec1}.error{color:#a33}pre{white-space:pre-wrap}</style></head><body><h1>'+title+'</h1>'+content+'</body></html>'
 return HTMLResponse(markup,headers={'Content-Security-Policy':"default-src 'none'; img-src http: https:; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'",'Cache-Control':'no-store'})
def _notice_or_404(db,nid):
 n=get_notice(db,nid)
 if not n:raise HTTPException(404,'通知不存在')
 return n
def _clean_actions(items):
 cleaned=[]
 for index,item in enumerate(items[:30],1):
  if not isinstance(item,dict) or not str(item.get('title') or '').strip():continue
  try:order=int(item.get('order') or index)
  except (TypeError,ValueError):order=index
  status=str(item.get('status') or 'new').lower()
  if status not in {'new','todo','doing','done'}:status='new'
  deps=item.get('depends_on',[])
  deps=[str(x)[:60] for x in deps if isinstance(x,(str,int))][:8] if isinstance(deps,list) else []
  cleaned.append({'id':str(item.get('id') or f'action-{index}')[:60],'title':str(item.get('title')).strip()[:200],'detail':str(item.get('detail') or '').strip()[:1000],'due_date':str(item.get('due_date') or '')[:20],'status':status,'order':order,'depends_on':deps})
 return sorted(cleaned,key=lambda x:(x['order'],x['id']))
@app.get('/api/notices/{nid}/analysis')
def notice_analysis(nid):
 with connect() as db:
  n=_notice_or_404(db,nid)
  return {'notice_id':nid,'relevant':n['relevant'],'reason':n['relevance_reason'],'deadline':n['deadline'],'materials':n['materials'],'analysis':n['analysis'],'tasks':n['tasks'],'action_items':n['tasks']}
@app.put('/api/notices/{nid}/analysis')
def update_analysis(nid,body:AnalysisUpdateIn):
 values=body.model_dump(exclude_unset=True) if hasattr(body,'model_dump') else body.dict(exclude_unset=True)
 with connect() as db:
  current=_notice_or_404(db,nid)
  analysis=dict(current.get('analysis') or {})
  if 'category' in values:analysis['category']=str(values['category'] or 'other')
  if 'priority' in values:analysis['priority']=str(values['priority'] or 'normal')
  if 'summary' in values:analysis['summary']=str(values['summary'] or '')[:1000]
  if 'material_done' in values:analysis['material_done']=[str(x)[:200] for x in (values['material_done'] or [])][:30]
  incoming_actions=values.get('action_items') if values.get('action_items') is not None else values.get('tasks')
  actions=_clean_actions(incoming_actions) if incoming_actions is not None else list(current.get('tasks') or analysis.get('action_items') or [])
  analysis['action_items']=actions
  materials=[str(x).strip()[:200] for x in values.get('materials',current.get('materials') or []) if str(x).strip()][:30]
  deadline=str(values.get('deadline',current.get('deadline') or '') or '')[:20]
  relevant=int(values.get('relevant',bool(current.get('relevant'))))
  db.execute('UPDATE notices SET relevant=?,deadline=?,materials_json=?,analysis_json=?,tasks_json=?,updated_at=? WHERE id=?',(relevant,deadline,json.dumps(materials,ensure_ascii=False),json.dumps(analysis,ensure_ascii=False),json.dumps(actions,ensure_ascii=False),now(),nid))
  event(db,nid,'analysis_updated','已手工更新分类、优先级或办理步骤')
  return get_notice(db,nid)
@app.put('/api/notices/{nid}/tasks')
def update_tasks(nid,body:ActionsIn):
 return update_analysis(nid,AnalysisUpdateIn(action_items=body.action_items,tasks=body.tasks))
@app.put('/api/notices/{nid}/tasks/{task_id}')
def update_task_item(nid,task_id,body:dict):
 with connect() as db:
  current=_notice_or_404(db,nid);actions=list(current.get('tasks') or current.get('analysis',{}).get('action_items') or [])
  match=next((x for x in actions if str(x.get('id'))==task_id),None)
  if not match:raise HTTPException(404,'办理步骤不存在')
  for key in ('title','detail','due_date','status','order','depends_on'):
   if key in body:match[key]=body[key]
 return update_analysis(nid,AnalysisUpdateIn(action_items=actions))
@app.put('/api/notices/{nid}/task')
def task(nid,body:TaskIn):
 if body.status not in {'new','todo','doing','done','ignored'}:raise HTTPException(400,'状态无效')
 with connect() as db:
  if not db.execute('SELECT 1 FROM notices WHERE id=?',(nid,)).fetchone():raise HTTPException(404,'通知不存在')
  db.execute('UPDATE notices SET task_status=?,notes=?,updated_at=? WHERE id=?',(body.status,body.notes.strip(),now(),nid));event(db,nid,'task_updated','状态更新为 '+body.status);return get_notice(db,nid)
@app.get('/api/dashboard')
def dashboard():
 with connect() as db:
  total=db.execute('SELECT COUNT(*) FROM notices').fetchone()[0];relevant=db.execute('SELECT COUNT(*) FROM notices WHERE relevant=1').fetchone()[0];todo=db.execute("SELECT COUNT(*) FROM notices WHERE task_status IN ('new','todo','doing')").fetchone()[0]
 return {'total':total,'relevant':relevant,'todo':todo}
