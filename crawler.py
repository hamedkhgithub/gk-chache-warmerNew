#!/usr/bin/env python3
import argparse,csv,html,os,re,sys,time
from datetime import datetime,timezone
from urllib.parse import urljoin,urlparse
import httpx

SITE_ROOT='https://geniuskala.com/'
API_ROOT=urljoin(SITE_ROOT,'wp-json/wp/v2/')
ALLOWED_HOSTS={'geniuskala.com','www.geniuskala.com'}
BASE_ACCEPT='text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8'
DESKTOP={'accept':BASE_ACCEPT,'accept-language':'fa-IR,fa;q=0.9,en;q=0.7','sec-ch-ua-mobile':'?0','sec-ch-ua-platform':'"Windows"','sec-fetch-dest':'document','sec-fetch-mode':'navigate','sec-fetch-user':'?1','upgrade-insecure-requests':'1','user-agent':'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/140 Safari/537.36'}
MOBILE={'accept':BASE_ACCEPT,'accept-language':'fa-IR,fa;q=0.9,en;q=0.7','sec-ch-ua-mobile':'?1','sec-ch-ua-platform':'"Android"','sec-fetch-dest':'document','sec-fetch-mode':'navigate','sec-fetch-user':'?1','upgrade-insecure-requests':'1','user-agent':'Mozilla/5.0 (Linux; Android 15; Pixel 8) AppleWebKit/537.36 Chrome/140 Mobile Safari/537.36'}

def log(x=''): print(x,flush=True)
def valid_url(u):
    p=urlparse(u); return p.scheme in ('http','https') and (p.hostname or '').lower() in ALLOWED_HOSTS

def validate_cookie(s):
    if not s: raise SystemExit('Missing GitHub Secret GK_LOGIN_COOKIES.')
    names=[p.split('=',1)[0].strip() for p in s.split(';') if '=' in p]
    if not any(n.startswith('wordpress_logged_in_') for n in names): raise SystemExit('GK_LOGIN_COOKIES must contain wordpress_logged_in_*.')
    if '_lscache_vary' not in names: log('WARNING: _lscache_vary is missing from GK_LOGIN_COOKIES.')

def make_headers(device='desktop',nav='direct',auth='guest',cookie=''):
    h=(DESKTOP if device=='desktop' else MOBILE).copy()
    if nav=='internal': h['sec-fetch-site']='same-origin'; h['referer']=SITE_ROOT
    else: h['sec-fetch-site']='none'; h.pop('referer',None)
    if auth=='logged_in': h['cookie']=cookie
    return h

def login_check(cookie,timeout):
    validate_cookie(cookie)
    h=make_headers('desktop','direct','logged_in',cookie)
    # wp-admin is a reliable behavioral test: authenticated users stay in admin;
    # expired/invalid sessions are redirected to wp-login.php.
    with httpx.Client(http2=True,follow_redirects=True,timeout=timeout,headers=h) as c:
        r=c.get(urljoin(SITE_ROOT,'wp-admin/'))
    final=str(r.url); body=r.text[:250000].lower()
    logged=('wp-admin' in urlparse(final).path and 'wp-login.php' not in final and ('wp-admin-bar-' in body or 'dashboard' in body or 'پیشخوان' in body))
    log('\n=== LOGIN CHECK ===')
    log('Cookie structure : OK')
    log(f'HTTP             : {r.status_code}')
    log(f'Effective URL    : {final}')
    log(f'WordPress session: {"LOGGED IN ✓" if logged else "NOT LOGGED IN ✗"}')
    if not logged: raise SystemExit('Login verification failed. Refresh GK_LOGIN_COOKIES and run again.')

def api_get(client,path,params=None):
    r=client.get(urljoin(API_ROOT,path),params=params); r.raise_for_status(); return r

def discover_categories(timeout):
    cats=[]; page=1
    with httpx.Client(http2=True,follow_redirects=True,timeout=timeout,headers={'user-agent':DESKTOP['user-agent']}) as c:
        while True:
            r=api_get(c,'product_cat',{'per_page':100,'page':page,'hide_empty':'false','orderby':'name','order':'asc'})
            batch=r.json(); cats.extend(batch)
            if page>=int(r.headers.get('x-wp-totalpages','1')): break
            page+=1
    return cats

def tree_rows(cats):
    by_parent={}; by_id={x['id']:x for x in cats}
    for x in cats: by_parent.setdefault(x.get('parent',0),[]).append(x)
    for v in by_parent.values(): v.sort(key=lambda x: html.unescape(x['name']).casefold())
    rows=[]
    def walk(pid,depth):
        for x in by_parent.get(pid,[]): rows.append((x,depth)); walk(x['id'],depth+1)
    walk(0,0)
    # include any orphaned terms defensively
    seen={x['id'] for x,_ in rows}
    rows.extend((x,0) for x in cats if x['id'] not in seen)
    return rows,by_id,by_parent

def descendants(cid,by_parent):
    out=[]
    for x in by_parent.get(cid,[]): out.append(x['id']); out.extend(descendants(x['id'],by_parent))
    return out

def choose_category(cats,include_children_default=True):
    rows,by_id,by_parent=tree_rows(cats)
    log('\n=== PRODUCT CATEGORIES ===')
    for i,(x,d) in enumerate(rows,1): log(f'{i:3}. {"  "*d}{html.unescape(x["name"])}  [{x.get("count",0)}]')
    raw=input('\nCategory number (q=quit): ').strip()
    if raw.lower()=='q': raise SystemExit(0)
    try: cat=rows[int(raw)-1][0]
    except Exception: raise SystemExit('Invalid category selection.')
    ans=input(f'Include subcategories? [{"Y/n" if include_children_default else "y/N"}]: ').strip().lower()
    include=include_children_default if not ans else ans.startswith('y')
    ids=[cat['id']]+(descendants(cat['id'],by_parent) if include else [])
    return cat,ids

def discover_products(cat_ids,timeout):
    products=[]; seen=set()
    with httpx.Client(http2=True,follow_redirects=True,timeout=timeout,headers={'user-agent':DESKTOP['user-agent']}) as c:
        for cid in cat_ids:
            page=1
            while True:
                r=api_get(c,'product',{'product_cat':cid,'per_page':100,'page':page,'orderby':'title','order':'asc'})
                batch=r.json()
                for p in batch:
                    if p['id'] not in seen: seen.add(p['id']); products.append(p)
                if page>=int(r.headers.get('x-wp-totalpages','1')): break
                page+=1
    products.sort(key=lambda p: html.unescape(p['title']['rendered']).casefold())
    return products

def parse_selection(s,n):
    s=s.strip().lower()
    if s in ('a','all','*',''): return set(range(1,n+1))
    if s in ('n','none'): return set()
    out=set()
    for part in s.split(','):
        part=part.strip()
        if '-' in part:
            a,b=part.split('-',1); out.update(range(int(a),int(b)+1))
        elif part: out.add(int(part))
    bad=[i for i in out if i<1 or i>n]
    if bad: raise ValueError(f'out of range: {bad}')
    return out

def interactive_urls(timeout):
    cats=discover_categories(timeout)
    if not cats: raise SystemExit('No WooCommerce product categories found via WP REST API.')
    cat,ids=choose_category(cats)
    products=discover_products(ids,timeout)
    items=[('CATEGORY',html.unescape(cat['name']),cat['link'])]
    items += [('PRODUCT',html.unescape(re.sub('<[^>]+>','',p['title']['rendered'])),p['link']) for p in products]
    log(f'\n=== URL SELECTION: {html.unescape(cat["name"])} ===')
    for i,(kind,name,url) in enumerate(items,1): log(f'{i:3}. [{kind:8}] {name}\n     {url}')
    log('\nEnter all, none, numbers, or ranges. Example: 1,3-8,12')
    while True:
        try: selected=parse_selection(input('Select URLs [all]: '),len(items)); break
        except Exception as e: log(f'Invalid selection: {e}')
    urls=[items[i-1][2] for i in sorted(selected)]
    if not urls: raise SystemExit('No URLs selected.')
    os.makedirs('logs',exist_ok=True)
    with open('logs/selected-urls.txt','w',encoding='utf-8') as f: f.write('\n'.join(urls)+'\n')
    return urls

def load_urls(path):
    vals=[]
    with open(path,'r',encoding='utf-8-sig') as f:
        for line in f:
            u=line.strip()
            if u and not u.startswith('#') and valid_url(u) and u not in vals: vals.append(u)
    return vals

def fetch(url,h,timeout):
    t=time.perf_counter()
    with httpx.Client(http2=True,follow_redirects=True,timeout=timeout,headers=h) as c:
        r=c.get(url); _=r.content
    return {'http':r.status_code,'cache':(r.headers.get('x-litespeed-cache') or 'N/A').upper(),'ctrl':r.headers.get('x-litespeed-cache-control') or '',
            'vary':r.headers.get('x-litespeed-vary') or '', 'set_cookie':'YES' if r.headers.get('set-cookie') else 'NO',
            'sec':time.perf_counter()-t,'effective_url':str(r.url)}

def run_scan(urls,a,cookie):
    devices=['mobile','desktop'] if a.device=='both' else [a.device]
    navs=['direct','internal'] if a.navigation=='both' else [a.navigation]
    auths=['guest','logged_in'] if a.auth=='both' else [a.auth]
    os.makedirs('logs',exist_ok=True); path='logs/cache-warmer.csv'
    fields=['utc_time','index','auth','device','navigation','url','warm_http','warm_cache','warm_control','warm_vary','warm_set_cookie','warm_seconds','verify_http','verify_cache','verify_control','verify_vary','verify_set_cookie','verify_seconds','effective_url','result','error']
    failures=0
    with open(path,'w',newline='',encoding='utf-8-sig') as f:
        w=csv.DictWriter(f,fieldnames=fields); w.writeheader()
        for i,u in enumerate(urls,1):
            log(f'\n[{i}/{len(urls)}] {u}')
            for au in auths:
                for d in devices:
                    for n in navs:
                        label=f'{au}/{d}/{n}'
                        try:
                            h=make_headers(d,n,au,cookie); x=fetch(u,h,a.timeout)
                            log(f'  {label:30} #1 HTTP {x["http"]} | {x["cache"]} | {x["sec"]:.2f}s')
                            time.sleep(max(0,a.verify_delay)); y=fetch(u,h,a.timeout)
                            # Guest should normally become HIT. Logged-in is diagnostic: don't call BYPASS/MISS a warmer failure.
                            ok=y['http']<400 and (au=='logged_in' or y['cache'].startswith('HIT'))
                            result=('HIT' if y['cache'].startswith('HIT') else ('LOGGED_IN_RESPONSE' if au=='logged_in' and y['http']<400 else 'NOT_CONFIRMED'))
                            if not ok: failures+=1
                            log(f'  {label:30} #2 HTTP {y["http"]} | {y["cache"]} | {y["sec"]:.2f}s | {result}')
                            w.writerow({'utc_time':datetime.now(timezone.utc).isoformat(),'index':i,'auth':au,'device':d,'navigation':n,'url':u,
                              'warm_http':x['http'],'warm_cache':x['cache'],'warm_control':x['ctrl'],'warm_vary':x['vary'],'warm_set_cookie':x['set_cookie'],'warm_seconds':f'{x["sec"]:.3f}',
                              'verify_http':y['http'],'verify_cache':y['cache'],'verify_control':y['ctrl'],'verify_vary':y['vary'],'verify_set_cookie':y['set_cookie'],'verify_seconds':f'{y["sec"]:.3f}',
                              'effective_url':y['effective_url'],'result':result,'error':''}); f.flush()
                        except Exception as e:
                            failures+=1; log(f'  {label:30} ERROR: {e}')
                            w.writerow({'utc_time':datetime.now(timezone.utc).isoformat(),'index':i,'auth':au,'device':d,'navigation':n,'url':u,'result':'ERROR','error':str(e)}); f.flush()
            if i<len(urls): time.sleep(max(0,a.delay))
    log(f'\nDone | URLs: {len(urls)} | guest not-confirmed/errors: {failures}')
    return failures

def main():
    ap=argparse.ArgumentParser(description='GeniusKala category-aware cache crawler')
    ap.add_argument('--file',default='urls.txt',help='URL file used when --source=file')
    ap.add_argument('--source',choices=['interactive','file'],default='interactive')
    ap.add_argument('--device',choices=['mobile','desktop','both'],default='both')
    ap.add_argument('--navigation',choices=['direct','internal','both'],default='both')
    ap.add_argument('--auth',choices=['guest','logged_in','both'],default='both')
    ap.add_argument('--delay',type=float,default=5); ap.add_argument('--verify-delay',type=float,default=2); ap.add_argument('--timeout',type=float,default=60)
    ap.add_argument('--login-check-only',action='store_true')
    a=ap.parse_args(); cookie=os.getenv('GK_LOGIN_COOKIES','').strip()
    if 'logged_in' in (['guest','logged_in'] if a.auth=='both' else [a.auth]): login_check(cookie,a.timeout)
    if a.login_check_only: return
    urls=interactive_urls(a.timeout) if a.source=='interactive' else load_urls(a.file)
    if not urls: raise SystemExit('No valid URLs found.')
    sys.exit(1 if run_scan(urls,a,cookie) else 0)
if __name__=='__main__': main()
