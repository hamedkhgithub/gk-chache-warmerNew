#!/usr/bin/env python3
import argparse
import csv
import os
import sys
import time
from datetime import datetime, timezone
from urllib.parse import urlparse

import requests

ALLOWED_HOSTS = {"geniuskala.com", "www.geniuskala.com"}
SITE_ROOT = "https://geniuskala.com/"
LOGIN_URL = "https://geniuskala.com/wp-login.php"

PROFILES = {
    "mobile": {
        "User-Agent": "Mozilla/5.0 (Linux; Android 15; Pixel 9) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Mobile Safari/537.36",
        "sec-ch-ua": '"Chromium";v="140", "Google Chrome";v="140", "Not=A?Brand";v="24"',
        "sec-ch-ua-mobile": "?1",
        "sec-ch-ua-platform": '"Android"',
    },
    "desktop": {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36",
        "sec-ch-ua": '"Chromium";v="140", "Google Chrome";v="140", "Not=A?Brand";v="24"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"Windows"',
    },
}

COMMON_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
    "Accept-Language": "fa-IR,fa;q=0.9,en-US;q=0.8,en;q=0.7",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
}
# Deliberately NO Cache-Control:no-cache and NO Pragma:no-cache.


def log(message=""):
    print(message, flush=True)


def load_urls(path):
    if not os.path.exists(path):
        raise SystemExit(f"URL file not found: {path}")

    urls = []
    if path.lower().endswith(".csv"):
        with open(path, "r", encoding="utf-8-sig", newline="") as f:
            for row in csv.reader(f):
                for cell in row:
                    cell = cell.strip()
                    if cell.startswith(("http://", "https://")):
                        urls.append(cell)
                        break
    else:
        with open(path, "r", encoding="utf-8-sig") as f:
            urls = [
                line.strip()
                for line in f
                if line.strip() and not line.lstrip().startswith("#")
            ]

    clean, seen = [], set()
    for url in urls:
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower()
        if parsed.scheme not in ("http", "https") or host not in ALLOWED_HOSTS:
            log(f"SKIP invalid/outside GeniusKala: {url}")
            continue
        if url not in seen:
            seen.add(url)
            clean.append(url)

    return clean


def make_headers(profile, *, same_origin=False, referer=None):
    headers = dict(COMMON_HEADERS)
    headers.update(PROFILES[profile])

    if same_origin:
        headers["Sec-Fetch-Site"] = "same-origin"
    else:
        headers["Sec-Fetch-Site"] = "none"

    if referer:
        headers["Referer"] = referer
    else:
        headers.pop("Referer", None)

    return headers


def has_logged_in_cookie(session):
    return any(cookie.name.startswith("wordpress_logged_in_") for cookie in session.cookies)


def login_session(profile, username, password, timeout):
    """
    Create a real WordPress logged-in session.

    Credentials come from GitHub Secrets and are never printed.
    The session preserves wordpress_logged_in_* and _lscache_vary cookies,
    allowing LiteSpeed to warm the logged-in/ESI cache variant.
    """
    session = requests.Session()

    # WordPress uses this cookie to verify that cookies are enabled.
    session.get(
        LOGIN_URL,
        headers=make_headers(profile),
        timeout=(10, timeout),
        allow_redirects=True,
    )

    data = {
        "log": username,
        "pwd": password,
        "wp-submit": "ورود",
        "redirect_to": SITE_ROOT,
        "testcookie": "1",
        "rememberme": "forever",
    }

    response = session.post(
        LOGIN_URL,
        data=data,
        headers=make_headers(profile, same_origin=True, referer=LOGIN_URL),
        timeout=(10, timeout),
        allow_redirects=True,
    )
    _ = response.content

    if not has_logged_in_cookie(session):
        raise RuntimeError(
            "WordPress login failed: wordpress_logged_in_* cookie was not created. "
            "Check GK_WP_USERNAME / GK_WP_PASSWORD and any 2FA/CAPTCHA/login restrictions."
        )

    # Hit the home page once so LiteSpeed can establish _lscache_vary if needed.
    prime = session.get(
        SITE_ROOT,
        headers=make_headers(profile),
        timeout=(10, timeout),
        allow_redirects=True,
    )
    _ = prime.content

    return session


def fetch_guest(url, profile, timeout):
    """
    Guest/public request.

    Intentionally uses a fresh request and does not persist Set-Cookie values,
    so it warms the public cache rather than a PHP/WooCommerce session.
    """
    started = time.perf_counter()
    response = requests.get(
        url,
        headers=make_headers(profile),
        timeout=(10, timeout),
        allow_redirects=True,
    )
    _ = response.content
    elapsed = time.perf_counter() - started

    return response, elapsed


def fetch_logged_in(session, url, profile, timeout):
    """
    Logged-in request.

    Cookies MUST persist between warm + verify requests because the LiteSpeed
    logged-in ESI variant depends on the authenticated WordPress session/vary.
    """
    started = time.perf_counter()
    response = session.get(
        url,
        headers=make_headers(profile),
        timeout=(10, timeout),
        allow_redirects=True,
    )
    _ = response.content
    elapsed = time.perf_counter() - started

    return response, elapsed


def response_info(response, elapsed):
    cache = (response.headers.get("X-LiteSpeed-Cache") or "N/A").upper()
    return {
        "status": response.status_code,
        "cache": cache,
        "elapsed": elapsed,
        "final_url": response.url,
        "redirects": len(response.history),
    }


def is_hit(cache_value):
    # Accept HIT and HIT,PRIVATE in the report. With ESI configured as intended,
    # logged-in public pages should normally show HIT rather than HIT,PRIVATE.
    return cache_value.startswith("HIT")


def main():
    parser = argparse.ArgumentParser(
        description="External LiteSpeed cache warmer for GeniusKala"
    )
    parser.add_argument("--file", default="urls.txt")
    parser.add_argument(
        "--mode",
        choices=["mobile", "desktop", "both"],
        default="both",
    )
    parser.add_argument(
        "--auth",
        choices=["guest", "logged_in", "both"],
        default="guest",
        help="Warm guest cache, logged-in ESI variant, or both",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=5.0,
        help="Seconds between URLs",
    )
    parser.add_argument(
        "--verify-delay",
        type=float,
        default=2.0,
        help="Seconds between warm request and verification request",
    )
    parser.add_argument("--timeout", type=float, default=45.0)
    args = parser.parse_args()

    urls = load_urls(args.file)
    if not urls:
        raise SystemExit("No valid GeniusKala URLs found.")

    modes = ["mobile", "desktop"] if args.mode == "both" else [args.mode]
    auth_modes = ["guest", "logged_in"] if args.auth == "both" else [args.auth]

    username = os.getenv("GK_WP_USERNAME", "")
    password = os.getenv("GK_WP_PASSWORD", "")

    if "logged_in" in auth_modes and (not username or not password):
        raise SystemExit(
            "Logged-in warming requires GitHub Secrets GK_WP_USERNAME and GK_WP_PASSWORD."
        )

    # One persistent authenticated session per browser profile.
    login_sessions = {}
    if "logged_in" in auth_modes:
        for profile in modes:
            log(f"Creating logged-in {profile} session...")
            login_sessions[profile] = login_session(
                profile, username, password, args.timeout
            )
            vary_present = "_lscache_vary" in login_sessions[profile].cookies
            log(
                f"  {profile}: WordPress login OK | "
                f"_lscache_vary={'YES' if vary_present else 'not seen yet'}"
            )

    os.makedirs("logs", exist_ok=True)
    log_path = "logs/cache-warmer.csv"
    new_log = not os.path.exists(log_path)
    failures = 0

    with open(log_path, "a", encoding="utf-8-sig", newline="") as log_file:
        writer = csv.writer(log_file)

        if new_log:
            writer.writerow(
                [
                    "utc_time",
                    "index",
                    "auth",
                    "mode",
                    "url",
                    "warm_http",
                    "warm_cache",
                    "warm_seconds",
                    "verify_http",
                    "verify_cache",
                    "verify_seconds",
                    "result",
                    "error",
                ]
            )

        for index, url in enumerate(urls, 1):
            log(f"\n[{index}/{len(urls)}] {url}")

            for auth in auth_modes:
                for profile in modes:
                    label = f"{auth}/{profile}"

                    try:
                        if auth == "guest":
                            r1, e1 = fetch_guest(url, profile, args.timeout)
                        else:
                            r1, e1 = fetch_logged_in(
                                login_sessions[profile], url, profile, args.timeout
                            )

                        first = response_info(r1, e1)
                        log(
                            f"  {label:18} #1 HTTP {first['status']} | "
                            f"{first['cache']} | {first['elapsed']:.2f}s"
                        )

                        time.sleep(max(0, args.verify_delay))

                        if auth == "guest":
                            r2, e2 = fetch_guest(url, profile, args.timeout)
                        else:
                            r2, e2 = fetch_logged_in(
                                login_sessions[profile], url, profile, args.timeout
                            )

                        second = response_info(r2, e2)
                        warmed = second["status"] < 400 and is_hit(second["cache"])
                        result = "WARMED" if warmed else "NOT_CONFIRMED"

                        if not warmed:
                            failures += 1

                        log(
                            f"  {label:18} #2 HTTP {second['status']} | "
                            f"{second['cache']} | {second['elapsed']:.2f}s | {result}"
                        )

                        writer.writerow(
                            [
                                datetime.now(timezone.utc).isoformat(),
                                index,
                                auth,
                                profile,
                                url,
                                first["status"],
                                first["cache"],
                                f"{first['elapsed']:.3f}",
                                second["status"],
                                second["cache"],
                                f"{second['elapsed']:.3f}",
                                result,
                                "",
                            ]
                        )
                        log_file.flush()

                    except Exception as exc:
                        failures += 1
                        log(f"  {label:18} ERROR: {exc}")
                        writer.writerow(
                            [
                                datetime.now(timezone.utc).isoformat(),
                                index,
                                auth,
                                profile,
                                url,
                                "",
                                "",
                                "",
                                "",
                                "",
                                "",
                                "ERROR",
                                str(exc),
                            ]
                        )
                        log_file.flush()

            if index < len(urls):
                log(f"  waiting {args.delay:g}s...")
                time.sleep(max(0, args.delay))

    log(
        f"\nDone. URLs: {len(urls)} | "
        f"not confirmed/errors: {failures} | log: {log_path}"
    )
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
