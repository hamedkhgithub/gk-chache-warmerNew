# GeniusKala Cache Warmer

External LiteSpeed cache warmer for **geniuskala.com**.

This version can warm both:

- **Guest/Public cache**
- **Logged-in ESI cache variant**

The logged-in mode performs a real WordPress login using GitHub Secrets, keeps the authentication cookies, and then warms the same type of logged-in cache view that a real authenticated customer uses.

## Important

Use a dedicated low-privilege **Customer** account for the warmer.

Do **not** use an Administrator account. LiteSpeed can vary logged-in cache by user role/view, and an Administrator also has extra frontend UI such as the admin bar.

## GitHub Secrets

In the repository open:

**Settings → Secrets and variables → Actions → New repository secret**

Create:

```text
GK_WP_USERNAME
GK_WP_PASSWORD
```

Use the username/email and password of the dedicated WordPress/WooCommerce customer account.

The crawler never prints the username, password, WordPress auth cookie, or `_lscache_vary` value.

If the login page uses 2FA, CAPTCHA, or another interactive login restriction, automatic logged-in warming will not work with that account.

## Manual run

Open:

**Actions → GeniusKala Cache Warmer → Run workflow**

Inputs:

### Browser profile

```text
both
mobile
desktop
```

### Cache view

```text
both
guest
logged_in
```

- `guest` warms the normal public cache.
- `logged_in` logs into WordPress and warms the logged-in ESI/cache variant.
- `both` warms both views.

### Other inputs

- `delay`: seconds between URLs
- `verify_delay`: seconds between the warm request and HIT verification

## Scheduled run

The daily scheduled run remains **guest-only** by default.

This prevents the cron job from failing before login secrets are configured.

After adding `GK_WP_USERNAME` and `GK_WP_PASSWORD`, if you want the scheduled run to warm both guest and logged-in variants, edit:

```yaml
AUTH="${{ github.event.inputs.auth || 'guest' }}"
```

to:

```yaml
AUTH="${{ github.event.inputs.auth || 'both' }}"
```

## How logged-in warming works

For each selected browser profile, the crawler:

1. opens `wp-login.php`
2. posts the login credentials
3. verifies that WordPress created `wordpress_logged_in_*`
4. visits the home page once so LiteSpeed can establish `_lscache_vary`
5. keeps that authenticated session
6. requests every URL
7. requests it again to verify `X-LiteSpeed-Cache: HIT`

With ESI configured correctly, public product/category pages viewed while logged in should normally show:

```text
X-LiteSpeed-Cache: hit
```

rather than:

```text
X-LiteSpeed-Cache: hit,private
```

## Guest mode

Guest mode intentionally does **not** persist cookies between requests. This avoids accidentally warming a PHP/WooCommerce session-specific view.

## Reports

`logs/cache-warmer.csv` now includes:

```text
auth
mode
warm_cache
verify_cache
```

so guest and logged-in results can be compared directly.

## Repository structure

```text
gk-cache-warmer/
├── crawler.py
├── requirements.txt
├── urls.txt
├── README.md
└── .github/
    └── workflows/
        └── cache-warmer.yml
```
