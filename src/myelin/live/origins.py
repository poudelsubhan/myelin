"""Navigation, API, authentication and passive assets have separate grants."""

from urllib.parse import urlsplit

from myelin.live.schema import https_origin
from myelin.program.bindings import allow_url


class OriginPolicy:
    def __init__(self, site):
        self.site = site

    def navigation(self, url):
        https_origin(url)
        return allow_url(url, set(self.site.navigation_origins))

    def permit(self, method, url, resource_type):
        origin = https_origin(url)
        if method not in ("GET", "HEAD", "OPTIONS"):
            allowed = self.site.navigation_origins + self.site.api_origins
        elif resource_type == "document":
            allowed = self.site.navigation_origins
        elif resource_type in ("image", "font", "stylesheet", "script", "media"):
            allowed = self.site.navigation_origins + self.site.asset_origins
        else:
            allowed = self.site.navigation_origins + self.site.api_origins
        if origin not in allowed:
            raise ValueError(f"unregistered dependency: {origin} ({resource_type})")
        allow_url(url, set(allowed))
        if any(p in urlsplit(url).path.lower() for p in ("/reset", "/__", "/admin/")):
            raise ValueError("administration/reset surface prohibited")
        return True
