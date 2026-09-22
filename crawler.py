#!/usr/bin/env python3
import argparse,csv,os,sys,time
from datetime import datetime,timezone
from urllib.parse import urlparse
import httpx

SITE_ROOT="https://geniuskala.com/"
ALLOWED_HOSTS={"geniuskala.com","www.geniuskala.com"}
BASE_ACCEPT="text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.9"

DESKTOP={
"accept":BASE_ACCEPT,"accept-language":"en-US,en;q=0.9",
"sec-ch-ua":'"Not_A Brand";v="99", "Google Chrome";v="109", "Chromium";v="109"',
"sec-ch-ua-mobile":"?0","sec-ch-ua-platform":'"Windows"',
"sec-fetch-dest":"document","sec-fetch-mode":"navigate","sec-fetch-user":"?1",
"upgrade-insecure-requests":"1",
"user-agent":"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/109.0.0.0 Safari/537.36"}
MOBILE={
"accept":BASE_ACCEPT,"accept-language":"en-US,en;q=0.9",
"sec-ch-ua":'"Not_A Brand";v="99", "Google Chrome";v="109", "Chromium";v="109"',
"sec-ch-ua-mobile":"?1","sec-ch-ua-platform":'"Android"',
"sec-fetch-dest":"document","sec-fetch-mode":"navigate","sec-fetch-user":"?1",
"upgrade-insecure-requests":"1",
"user-agent":"Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/109.0.0.0 Mobile Safari/537.36"}

def log(x=""): print(x,flush=True)

def load_urls(path):
    vals=[]
    with open(path,"r",encoding="utf-8-sig") as f:
        for line in f:
            u=line.strip()
            if u and not u.startswith("#"): vals.append(u)
    out=[]; seen=set()
    for u in vals:
        p=urlparse(u); h=(p.hostname or "").lower()
        if p.scheme in ("http","https") and h in ALLOWED_HOSTS and u not in seen:
            seen.add(u); out.append(u)
    return out

def validate_cookie(s):
    if not s: raise SystemExit("Missing GitHub Secret GK_LOGIN_COOKIES.")
    names=[p.split("=",1)[0].strip() for p in s.split(";") if "=" in p]
    if not any(n.startswith("wordpress_logged_in_") for n in names):
        raise SystemExit("GK_LOGIN_COOKIES must contain wordpress_logged_in_*.")
    if "_lscache_vary" not in names:
        raise SystemExit("GK_LOGIN_COOKIES must contain _lscache_vary.")

def headers(device,nav,auth,cookie):
    h=(DESKTOP if device=="desktop" else MOBILE).copy()
    if nav=="internal":
        h["sec-fetch-site"]="same-origin"; h["referer"]=SITE_ROOT
    else:
        h["sec-fetch-site"]="none"; h.pop("referer",None)
    if auth=="logged_in": h["cookie"]=cookie
    return h

def fetch(url,h,timeout):
    t=time.perf_counter()
    with httpx.Client(http2=True,follow_redirects=True,timeout=timeout,headers=h) as c:
        r=c.get(url); _=r.content
    return {
        "http":r.status_code,
        "cache":(r.headers.get("x-litespeed-cache") or "N/A").upper(),
        "ctrl":r.headers.get("x-litespeed-cache-control") or "",
        "sec":time.perf_counter()-t
    }

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--file",default="urls.txt")
    ap.add_argument("--device",choices=["mobile","desktop","both"],default="both")
    ap.add_argument("--navigation",choices=["direct","internal","both"],default="both")
    ap.add_argument("--auth",choices=["guest","logged_in","both"],default="guest")
    ap.add_argument("--delay",type=float,default=5)
    ap.add_argument("--verify-delay",type=float,default=2)
    ap.add_argument("--timeout",type=float,default=60)
    a=ap.parse_args()

    urls=load_urls(a.file)
    if not urls: raise SystemExit("No valid URLs found.")
    devices=["mobile","desktop"] if a.device=="both" else [a.device]
    navs=["direct","internal"] if a.navigation=="both" else [a.navigation]
    auths=["guest","logged_in"] if a.auth=="both" else [a.auth]
    cookie=os.getenv("GK_LOGIN_COOKIES","").strip()
    if "logged_in" in auths:
        validate_cookie(cookie)
        log("Logged-in cookie: OK (values hidden)")

    os.makedirs("logs",exist_ok=True)
    path="logs/cache-warmer.csv"
    fields=["utc_time","index","auth","device","navigation","url",
            "warm_http","warm_cache","warm_control","warm_seconds",
            "verify_http","verify_cache","verify_control","verify_seconds",
            "result","error"]
    new=not os.path.exists(path); failures=0

    with open(path,"a",newline="",encoding="utf-8-sig") as f:
        w=csv.DictWriter(f,fieldnames=fields)
        if new:w.writeheader()
        for i,u in enumerate(urls,1):
            log(f"\n[{i}/{len(urls)}] {u}")
            for au in auths:
                for d in devices:
                    for n in navs:
                        label=f"{au}/{d}/{n}"
                        try:
                            h=headers(d,n,au,cookie)
                            x=fetch(u,h,a.timeout)
                            log(f"  {label:30} #1 HTTP {x['http']} | {x['cache']} | {x['sec']:.2f}s")
                            time.sleep(max(0,a.verify_delay))
                            y=fetch(u,h,a.timeout)
                            ok=y["http"]<400 and y["cache"].startswith("HIT")
                            result="WARMED" if ok else "NOT_CONFIRMED"
                            if not ok: failures+=1
                            log(f"  {label:30} #2 HTTP {y['http']} | {y['cache']} | {y['sec']:.2f}s | {result}")
                            w.writerow({"utc_time":datetime.now(timezone.utc).isoformat(),
                              "index":i,"auth":au,"device":d,"navigation":n,"url":u,
                              "warm_http":x["http"],"warm_cache":x["cache"],"warm_control":x["ctrl"],"warm_seconds":f"{x['sec']:.3f}",
                              "verify_http":y["http"],"verify_cache":y["cache"],"verify_control":y["ctrl"],"verify_seconds":f"{y['sec']:.3f}",
                              "result":result,"error":""}); f.flush()
                        except Exception as e:
                            failures+=1; log(f"  {label:30} ERROR: {e}")
                            w.writerow({"utc_time":datetime.now(timezone.utc).isoformat(),"index":i,"auth":au,"device":d,"navigation":n,"url":u,
                            "warm_http":"","warm_cache":"","warm_control":"","warm_seconds":"","verify_http":"","verify_cache":"","verify_control":"",
                            "verify_seconds":"","result":"ERROR","error":str(e)}); f.flush()
            if i<len(urls): time.sleep(max(0,a.delay))
    log(f"\nDone | URLs: {len(urls)} | not confirmed/errors: {failures}")
    sys.exit(1 if failures else 0)

if __name__=="__main__": main()
