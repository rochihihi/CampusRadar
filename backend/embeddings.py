"""OpenAI-compatible embeddings, local vector cache and cosine search."""
import hashlib
import json
import math
import threading
from urllib.parse import urlsplit
import httpx
from storage import connect

BUILD_LOCK = threading.Lock()


def schema(db):
    db.execute('''CREATE TABLE IF NOT EXISTS embedding_settings(id INTEGER PRIMARY KEY,
        enabled INTEGER NOT NULL DEFAULT 0, base_url TEXT NOT NULL DEFAULT 'https://api.openai.com/v1',
        model TEXT NOT NULL DEFAULT 'text-embedding-3-small', api_key TEXT NOT NULL DEFAULT '',
        use_openai_key INTEGER NOT NULL DEFAULT 1, threshold REAL NOT NULL DEFAULT 0.35)''')
    db.execute('INSERT OR IGNORE INTO embedding_settings(id) VALUES(1)')
    db.execute('''CREATE TABLE IF NOT EXISTS rag_vectors(chunk_key TEXT NOT NULL, signature TEXT NOT NULL,
        notice_id TEXT NOT NULL, vector_json TEXT NOT NULL, PRIMARY KEY(chunk_key,signature))''')


def config(public=False):
    with connect() as db:
        schema(db)
        cfg = dict(db.execute('SELECT * FROM embedding_settings WHERE id=1').fetchone())
        key = cfg['api_key']
        if not key and cfg['use_openai_key'] and cfg['base_url'] == 'https://api.openai.com/v1':
            try:
                import keyring
                key = keyring.get_password('CampusRadar', 'openai') or ''
            except Exception:
                pass
            key = key or db.execute('SELECT openai_key FROM settings WHERE id=1').fetchone()[0]
    cfg['api_key'] = key or ''
    if public:
        cfg['has_key'] = bool(cfg.pop('api_key'))
    return cfg


def save_config(values):
    base = values['base_url'].strip().rstrip('/')
    url = urlsplit(base)
    if not url.netloc or url.username or url.password or url.query or url.fragment or (url.scheme != 'https' and not (url.scheme == 'http' and url.hostname in {'localhost','127.0.0.1','::1'})):
        raise ValueError('Embedding 地址须为 HTTPS，或本机 HTTP 地址；填写 API 基础地址，不含 /embeddings')
    model = values['model'].strip()
    if not model:
        raise ValueError('请填写 Embedding 模型名称')
    with connect() as db:
        schema(db)
        db.execute('UPDATE embedding_settings SET enabled=?,base_url=?,model=?,use_openai_key=?,threshold=? WHERE id=1', (int(values['enabled']),base,model,int(values['use_openai_key']),values['threshold']))
        if values.get('api_key') is not None:
            db.execute('UPDATE embedding_settings SET api_key=? WHERE id=1',(values['api_key'].strip(),))
    return config(public=True)


def signature(cfg):
    return hashlib.sha256((cfg['base_url']+'\0'+cfg['model']).encode()).hexdigest()


def chunk_key(row):
    return hashlib.sha256(('\0'.join(str(row[k]) for k in ('notice_id','position','title','url','text'))).encode()).hexdigest()


def normalize(vector):
    if not isinstance(vector,list) or not vector or len(vector)>16384:
        raise ValueError('Embedding 返回了无效向量')
    if any(isinstance(x,bool) or not isinstance(x,(int,float)) or not math.isfinite(x) for x in vector):
        raise ValueError('Embedding 向量含无效数值')
    norm = math.sqrt(sum(x*x for x in vector))
    if not math.isfinite(norm) or norm == 0:
        raise ValueError('Embedding 返回了零向量或无效数值')
    return [x/norm for x in vector]


def embed(texts, cfg):
    if not cfg['api_key']:
        raise ValueError('请配置有 Embedding 权限的 API Key；仅限聊天模型的 Key 无法使用向量服务')
    try:
        with httpx.Client(timeout=60, follow_redirects=False) as client:
            r=client.post(cfg['base_url']+'/embeddings',headers={'Authorization':'Bearer '+cfg['api_key']},json={'model':cfg['model'],'input':texts,'encoding_format':'float'})
        r.raise_for_status()
    except httpx.HTTPStatusError as exc:
        code=exc.response.status_code
        raise ValueError(f'Embedding 服务返回 HTTP {code}，请检查独立 Key、模型权限和 API 地址') from exc
    except httpx.RequestError as exc:
        raise ValueError('Embedding 服务连接失败，请检查网络和 API 地址') from exc
    try:
        data=r.json()['data']
        if not isinstance(data,list) or len(data)!=len(texts):
            raise ValueError()
        ordered=sorted(data,key=lambda x:x['index'])
        if [x['index'] for x in ordered] != list(range(len(texts))):
            raise ValueError()
        vectors=[normalize(x['embedding']) for x in ordered]
        if len({len(v) for v in vectors})!=1:
            raise ValueError()
        return vectors
    except (KeyError,TypeError,ValueError) as exc:
        raise ValueError('Embedding 响应格式、数量或向量维度不一致') from exc


def vector_status():
    cfg=config();sig=signature(cfg)
    with connect() as db:
        schema(db)
        rows=[dict(x) for x in db.execute('SELECT * FROM rag_chunks')]
        stored={x['chunk_key'] for x in db.execute('SELECT chunk_key FROM rag_vectors WHERE signature=?',(sig,))}
    ready=sum(chunk_key(r) in stored for r in rows)
    return {'enabled':bool(cfg['enabled']),'vectors':ready,'vector_pending':len(rows)-ready,'embedding_model':cfg['model'],'has_embedding_key':bool(cfg['api_key'])}


def build_batch():
    if not BUILD_LOCK.acquire(False):
        raise ValueError('向量建库正在执行，请稍后再试')
    try:
        cfg=config()
        if not cfg['enabled']:
            raise ValueError('请先在设置中启用向量检索')
        sig=signature(cfg)
        with connect() as db:
            schema(db)
            rows=[dict(x) for x in db.execute('SELECT * FROM rag_chunks')]
            existing={x['chunk_key'] for x in db.execute('SELECT chunk_key FROM rag_vectors WHERE signature=?',(sig,))}
        batch=[r for r in rows if chunk_key(r) not in existing][:16]
        if batch:
            vectors=embed([r['title']+'\n'+r['text'] for r in batch],cfg)
            with connect() as db:
                previous=db.execute('SELECT vector_json FROM rag_vectors WHERE signature=? LIMIT 1',(sig,)).fetchone()
                if previous and len(json.loads(previous[0])) != len(vectors[0]):
                    raise ValueError('服务返回的向量维度已变化，请清空向量缓存后重新建库')
                for r,v in zip(batch,vectors):
                    db.execute('INSERT OR REPLACE INTO rag_vectors VALUES(?,?,?,?)',(chunk_key(r),sig,r['notice_id'],json.dumps(v)))
        return {**vector_status(),'built':len(batch)}
    finally:
        BUILD_LOCK.release()


def vector_search(question, source_id=''):
    cfg=config()
    if not cfg['enabled']:
        return [],'','keyword'
    sig=signature(cfg)
    with connect() as db:
        rows=[dict(x) for x in db.execute("SELECT * FROM rag_chunks WHERE (?='' OR notice_id IN (SELECT id FROM notices WHERE source_id=?))",(source_id,source_id))]
        cached={x['chunk_key']:x['vector_json'] for x in db.execute('SELECT * FROM rag_vectors WHERE signature=?',(sig,))}
    usable=[r for r in rows if chunk_key(r) in cached]
    if not usable:
        return [],'当前范围尚无向量，请点击“建立向量索引”；本次使用关键词检索。','keyword'
    try:
        query=embed([question],cfg)[0]
        result=[]
        for row in usable:
            vector=json.loads(cached[chunk_key(row)])
            if len(vector)!=len(query):
                raise ValueError('向量维度不一致，请清空缓存并重新建库')
            score=sum(a*b for a,b in zip(query,vector))
            if score>=cfg['threshold']:
                result.append({**row,'similarity':score})
        result.sort(key=lambda r:r['similarity'],reverse=True)
        warning='部分片段尚未生成向量，当前仅检索已建库部分。' if len(usable)<len(rows) else ''
        return result[:60],warning,'hybrid'
    except ValueError as exc:
        return [],str(exc)+'；本次使用关键词检索。','keyword'
