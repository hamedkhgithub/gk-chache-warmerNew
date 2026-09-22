# GeniusKala Cache Warmer - Cookie Login

Create one GitHub Repository Secret:

`GK_LOGIN_COOKIES`

Its value must contain only:

`wordpress_logged_in_xxxxx=VALUE; _lscache_vary=VALUE`

Get both values from Chrome:
DevTools → Application → Cookies → https://geniuskala.com

Do not include PHPSESSID, cart, WooCommerce session, wishlist, recently-viewed or sbjs cookies.

Workflow UI supports:
- device: both / mobile / desktop
- navigation: both / direct / internal
- auth: both / guest / logged_in

The cookie values are never printed to logs.

The scheduled run is guest-only by default. Manual runs can use logged_in or both.

When the manual cookie expires or becomes invalid, update `GK_LOGIN_COOKIES` in GitHub Secrets.
