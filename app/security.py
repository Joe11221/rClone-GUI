from urllib.parse import urlsplit

from flask import abort, request

_SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


def _is_cross_site_request():
    """True if a browser sent this request on behalf of another site.

    Requests without these headers come from non-browser clients, which CSRF
    can't reach. Sec-Fetch-Site is checked first because Origin can't be
    compared with Host behind a reverse proxy that rewrites Host; browsers
    omit it on plain-http LAN addresses, where Origin is the fallback.
    """
    fetch_site = request.headers.get("Sec-Fetch-Site")
    if fetch_site:
        return fetch_site not in ("same-origin", "none")

    origin = request.headers.get("Origin")
    if origin is None:
        return False
    return urlsplit(origin).netloc.lower() != request.host.lower()


def init_request_guards(app):
    """Stop other websites from driving the app through the user's browser."""
    allowed_hosts = {host.lower() for host in app.config["ALLOWED_HOSTS"]}

    @app.before_request
    def _guard_request():
        # An allowlist is what stops DNS rebinding, where a hostile name
        # resolves to loopback and so looks same-origin to the browser.
        if allowed_hosts and request.host.lower() not in allowed_hosts:
            abort(403)
        if request.method not in _SAFE_METHODS and _is_cross_site_request():
            abort(403)
