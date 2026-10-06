from __future__ import annotations
import hashlib,html,re
from html.parser import HTMLParser
from urllib.parse import urljoin,urlsplit
import httpx

class Element:
 def __init__(self,tag='',attrs=None,parent=None):self.tag=tag;self.attrs=dict(attrs or []);self.parent=parent;self.children=[]
 def walk(self):
  yield self
  for child in self.children:
   if isinstance(child,Element):yield from child.walk()
 def text(self):
  if self.tag in {'script','style','svg','nav','header','footer','noscript'}:return ''
  body=''.join(c.text() if isinstance(c,Element) else c for c in self.children)
  if self.tag in {'p','div','li','tr','h1','h2','h3','h4','section','article','br'}:body='\n'+body+'\n'
  if self.tag in {'td','th'}:body+=' | '
  return body
class DocumentParser(HTMLParser):
 def __init__(self):super().__init__();self.root=Element();self.stack=[self.root]
 def handle_starttag(self,tag,attrs):
  node=Element(tag,attrs,self.stack[-1]);self.stack[-1].children.append(node)
  if tag not in {'area','base','br','col','embed','hr','img','input','link','meta','param','source','track','wbr'}:self.stack.append(node)
 def handle_startendtag(self,tag,attrs):
  self.handle_starttag(tag,attrs)
  if self.stack[-1].tag==tag:self.stack.pop()
 def handle_endtag(self,tag):
  for i in range(len(self.stack)-1,0,-1):
   if self.stack[i].tag==tag:del self.stack[i:];break
 def handle_data(self,data):self.stack[-1].children.append(data)

def render_article(node,base):
 if isinstance(node,str):return html.escape(node)
 if node.tag in {'script','style','svg','iframe','object','embed','form','input','button','nav','header','footer','noscript'}:return ''
 allowed={'p','div','span','strong','b','em','i','u','s','br','hr','h1','h2','h3','h4','h5','h6','ul','ol','li','table','thead','tbody','tfoot','tr','td','th','caption','blockquote','pre','code','a','img','section','article'}
 children=''.join(render_article(c,base) for c in node.children)
 if node.tag not in allowed:return children
 attrs={}
 if node.tag=='a':
  target=urljoin(base,node.attrs.get('href',''))
  if urlsplit(target).scheme in {'http','https'}:attrs.update(href=target,target='_blank',rel='noopener noreferrer')
 if node.tag=='img':
  src=urljoin(base,node.attrs.get('src',''))
  if urlsplit(src).scheme not in {'http','https'}:return ''
  attrs.update(src=src,alt=node.attrs.get('alt','通知图片'),loading='lazy',referrerpolicy='no-referrer')
 if node.tag in {'td','th'}:
  for key in ('rowspan','colspan'):
   value=node.attrs.get(key,'')
   if value.isdigit() and 0<int(value)<100:attrs[key]=value
 safe_styles=[]
 for prop in node.attrs.get('style','').split(';'):
  name,sep,value=prop.partition(':');name=name.strip().lower();value=value.strip().lower()
  if sep and name in {'text-align','font-weight','font-style','text-decoration','vertical-align'} and re.fullmatch(r'[a-z0-9 -]+',value):safe_styles.append(name+':'+value)
 if safe_styles:attrs['style']=';'.join(safe_styles)
 serialized=''.join(' '+k+'="'+html.escape(v,quote=True)+'"' for k,v in attrs.items())
 return '<'+node.tag+serialized+'>'+('' if node.tag in {'img','br','hr'} else children+'</'+node.tag+'>')

def extract_notice(markup,url):
 tree=DocumentParser();tree.feed(markup);nodes=list(tree.root.walk())
 ids={'vsb_content','vsb_content_2','articlecontent','article-content','newscontent'}
 classes={'v_news_content','wp_articlecontent','article-content','news_content','news-content','detail-text','TRS_Editor'}
 candidates=[n for n in nodes if n.attrs.get('id') in ids or set(n.attrs.get('class','').split()) & classes]
 page_type='article'
 if not candidates:
  # Recognized download lists are useful resources, but not policy article bodies.
  directories=[n for n in nodes if 'down-load' in n.attrs.get('class','').split()]
  lists=[n for d in directories for n in d.walk() if n.tag=='ul' and any(a.tag=='a' and a.attrs.get('href') for a in n.walk())]
  if lists:candidates=lists;page_type='resources'
 if not candidates:candidates=[n for n in nodes if n.tag=='article']
 if not candidates:candidates=[n for n in nodes if n.tag=='main']
 # Do not turn an unrecognized whole page into an alleged notification body.
 if not candidates:raise ValueError('该页面没有可识别的通知正文，可能是栏目或下载列表，请打开具体通知')
 body=max(candidates,key=lambda n:len(n.text()))
 content='\n'.join(re.sub(r'[ \t\r\f]+',' ',x).strip() for x in body.text().splitlines() if x.strip())[:40000]
 if not content:raise ValueError('通知正文为空，请打开原网页检查是否需要登录或是否仅含图片')
 resource_note='本页是资料下载目录，只提供资料名称与链接，不包含链接内的规定全文；不能据此确定申请条件或截止日期。'
 if page_type=='resources':content=resource_note+'\n'+content
 scope=body
 parent=body.parent
 while parent:
  if parent.tag=='form':scope=parent;break
  parent=parent.parent
 links=[];seen=set()
 body_nodes={id(n) for n in body.walk()}
 for n in scope.walk():
  if n.tag!='a' or not n.attrs.get('href'):continue
  target=urljoin(url,html.unescape(n.attrs['href']));label=re.sub(r'\s+',' ',n.text()).strip() or '通知附件'
  if urlsplit(target).scheme not in {'http','https'} or target in seen:continue
  attachment=bool(re.search(r'download|\.(pdf|docx?|xlsx?|zip|rar)(?:\?|$)',target,re.I))
  if id(n) not in body_nodes and not attachment:continue
  if label in {'首页','上一条','下一条','返回','关闭','打印','查看详情'}:continue
  seen.add(target);links.append({'label':label[:200],'url':target,'attachment':attachment})
 content_html=render_article(body,url)
 if page_type=='resources':content_html='<p>'+html.escape(resource_note)+'</p>'+content_html
 return {'content':content,'links':links[:30],'content_html':content_html,'page_type':page_type}

def fetch_notice(url):
 with httpx.Client(timeout=25,follow_redirects=True,headers={'User-Agent':'CampusRadar/1.0'}) as client:
  response=client.get(url);response.raise_for_status()
 declared=re.search(br'charset\s*=\s*["\']?([\w-]+)',response.content[:5000],re.I)
 encoding=declared.group(1).decode('ascii') if declared else response.encoding or 'utf-8'
 try:markup=response.content.decode(encoding,errors='replace')
 except LookupError:markup=response.text
 return extract_notice(markup,str(response.url))
class Parser(HTMLParser):
 def __init__(self):super().__init__();self.parts=[];self.links=[];self.href='';self.label=[];self.skip=0
 def handle_starttag(self,tag,attrs):
  a=dict(attrs)
  if tag in {'script','style','svg'}:self.skip+=1
  if tag=='a':self.href=a.get('href','');self.label=[]
  if tag in {'p','li','div','h1','h2','h3','br'}:self.parts.append('\n')
 def handle_data(self,data):
  if not self.skip:self.parts.append(data);self.label.append(data) if self.href else None
 def handle_endtag(self,tag):
  if tag in {'script','style','svg'} and self.skip:self.skip-=1
  if tag=='a' and self.href:self.links.append((self.href,' '.join(self.label).strip()));self.href='';self.label=[]
def plain(value):
 p=Parser();p.feed(value or '');lines=[re.sub(r'\s+',' ',html.unescape(x)).strip() for x in ''.join(p.parts).splitlines()];return '\n'.join(x for x in lines if x)[:40000]
def fetch_source(source):
 u=source['url'];p=urlsplit(u)
 if p.scheme not in {'http','https'} or not p.netloc:raise ValueError('通知来源必须是 HTTP 或 HTTPS 链接')
 with httpx.Client(timeout=20,follow_redirects=True,headers={'User-Agent':'CampusRadar/1.0'}) as client:r=client.get(u);r.raise_for_status()
 parser=Parser();parser.feed(r.text);base=str(r.url);items=[];seen=set()
 for href,label in parser.links:
  link=urljoin(base,html.unescape(href).strip());pp=urlsplit(link)
  if pp.scheme not in {'http','https'} or link in seen:continue
  text=label or ''
  if text.strip() in {'考试管理','通知公告','教务处(招生办公室)简介','通知','公告','查看更多','查看详情','首页'} or len(text.strip())<8:continue
  if not re.search(r'通知|公告|教务|奖学金|报名|考试|评选|资助|竞赛|notice|announcement|academic|scholarship|deadline|admission',text+' '+pp.path,re.I):continue
  seen.add(link);title=re.sub(r'\s+',' ',text).strip()[:300] or link;items.append({'external_id':hashlib.sha256(link.encode()).hexdigest()[:32],'title':title,'url':link,'content':title,'published_at':None})
 return items[:100]
