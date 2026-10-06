from __future__ import annotations
import json, os, sqlite3
from datetime import datetime, timezone
from pathlib import Path

def data_dir() -> Path:
    root = Path(os.environ.get('CAMPUSRADAR_DATA_DIR') or (Path(os.environ.get('LOCALAPPDATA', Path.home())) / 'CampusRadar'))
    root.mkdir(parents=True, exist_ok=True)
    return root
def now(): return datetime.now(timezone.utc).isoformat()
def connect():
    class DB(sqlite3.Connection):
        def __exit__(self, exc_type, exc, tb):
            try:
                if exc_type is None: self.commit()
                else: self.rollback()
            finally: self.close()
            return False
    db=sqlite3.connect(data_dir()/'campusradar.sqlite3',factory=DB); db.row_factory=sqlite3.Row; db.execute('PRAGMA foreign_keys=ON')
    db.executescript('''CREATE TABLE IF NOT EXISTS profile(id INTEGER PRIMARY KEY CHECK(id=1),name TEXT NOT NULL DEFAULT '',school TEXT NOT NULL DEFAULT '',major TEXT NOT NULL DEFAULT '',keywords TEXT NOT NULL DEFAULT '',updated_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS sources(id TEXT PRIMARY KEY,kind TEXT NOT NULL,url TEXT NOT NULL UNIQUE,label TEXT NOT NULL,last_sync TEXT,last_error TEXT NOT NULL DEFAULT '',enabled INTEGER NOT NULL DEFAULT 1);
    CREATE TABLE IF NOT EXISTS notices(id TEXT PRIMARY KEY,source_id TEXT REFERENCES sources(id) ON DELETE SET NULL,external_id TEXT NOT NULL,title TEXT NOT NULL,url TEXT NOT NULL,content TEXT NOT NULL DEFAULT '',published_at TEXT,first_seen TEXT NOT NULL,updated_at TEXT NOT NULL,is_open INTEGER NOT NULL DEFAULT 1,relevant INTEGER,relevance_reason TEXT NOT NULL DEFAULT '',deadline TEXT,materials_json TEXT NOT NULL DEFAULT '[]',analysis_json TEXT NOT NULL DEFAULT '{}',tasks_json TEXT NOT NULL DEFAULT '[]',task_status TEXT NOT NULL DEFAULT 'new',notes TEXT NOT NULL DEFAULT '',UNIQUE(source_id,external_id));
    CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY AUTOINCREMENT,notice_id TEXT,event_type TEXT NOT NULL,detail TEXT NOT NULL,created_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS settings(id INTEGER PRIMARY KEY CHECK(id=1),provider TEXT NOT NULL DEFAULT 'none',openai_model TEXT NOT NULL DEFAULT 'gpt-4o-mini',deepseek_model TEXT NOT NULL DEFAULT 'deepseek-chat');''')
    for column,default in (('openai_key',''),('openai_official_key',''),('openai_official_model','gpt-6-luna'),('deepseek_key',''),('openai_base_url','https://api.openai.com/v1'),('deepseek_base_url','https://api.deepseek.com')):
        try: db.execute(f"ALTER TABLE settings ADD COLUMN {column} TEXT NOT NULL DEFAULT '{default}'")
        except sqlite3.OperationalError: pass
    for column,default in (('analysis_json','{}'),('tasks_json','[]'),('notice_links_json','[]'),('content_version','0'),('content_html','')):
        try: db.execute(f"ALTER TABLE notices ADD COLUMN {column} TEXT NOT NULL DEFAULT '{default}'")
        except sqlite3.OperationalError: pass
    stamp=now(); db.execute('INSERT OR IGNORE INTO profile(id,updated_at) VALUES(1,?)',(stamp,)); db.execute('INSERT OR IGNORE INTO settings(id) VALUES(1)'); db.commit(); return db
def _json_value(raw, fallback):
    try:
        value=json.loads(raw or '')
        return value if isinstance(value,type(fallback)) else fallback
    except (TypeError,ValueError): return fallback
def parse_notice(row):
    if not row:return None
    x=dict(row)
    x['materials']=_json_value(x.pop('materials_json', '[]'), [])
    # Older development builds briefly created an unused actions_json column.
    # Ignore it so clients have one canonical action list (tasks).
    x.pop('actions_json', None)
    x['analysis']=_json_value(x.pop('analysis_json', '{}'), {})
    x['notice_links']=_json_value(x.pop('notice_links_json', '[]'), [])
    x['tasks']=_json_value(x.pop('tasks_json', '[]'), [])
    # Expose a descriptive alias for API clients building an action checklist.
    x['action_items']=x['tasks']
    return x
def get_profile(db):return dict(db.execute('SELECT * FROM profile WHERE id=1').fetchone())
def get_settings(db):return dict(db.execute('SELECT * FROM settings WHERE id=1').fetchone())
def get_notice(db,nid):return parse_notice(db.execute('SELECT * FROM notices WHERE id=?',(nid,)).fetchone())
def list_notices(db):return [parse_notice(x) for x in db.execute('SELECT * FROM notices ORDER BY COALESCE(deadline,published_at,first_seen) ASC,first_seen DESC')]
def event(db,nid,typ,detail):db.execute('INSERT INTO events(notice_id,event_type,detail,created_at) VALUES(?,?,?,?)',(nid,typ,detail,now()))
