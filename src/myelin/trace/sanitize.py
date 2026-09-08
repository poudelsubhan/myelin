import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

SENSITIVE = re.compile(
    r"^(password|csrf|authorization|cookie|set-cookie|token|access_token|refresh_token|confirmation_token|"
    r"api_key|x-csrf-token|x-myelin-demo-token)$",
    re.I,
)


class Sanitizer:
    def __init__(self):
        self.secrets = {}

    def register(self, value, name=None):
        if isinstance(value, str) and value.startswith("<secret:"):
            return value
        if isinstance(value, str) and value:
            if value not in self.secrets:
                self.secrets[value] = name or f"observed-{len(self.secrets) + 1}"
            return f"<secret:{self.secrets[value]}>"
        return "<secret:empty>"

    def clean(self, value, key=""):
        if (
            isinstance(value, dict)
            and set(value) == {"kind", "key"}
            and value.get("kind") == "secret"
            and isinstance(value.get("key"), str)
        ):
            # A typed binding contains a reference name, not the credential value.
            return dict(value)
        if SENSITIVE.fullmatch(key):
            return self.register(value)
        if isinstance(value, dict):
            return {k: self.clean(v, k) for k, v in value.items()}
        if isinstance(value, list):
            return [self.clean(v) for v in value]
        if isinstance(value, str):
            # HTML hidden token sources must remain useful without exposing values.
            value = re.sub(
                r'(<input\b[^>]*name=["\'](?:csrf|token)["\'][^>]*value=["\'])([^"\']+)',
                lambda m: m[1] + self.register(m[2]),
                value,
                flags=re.I,
            )
            for raw, name in sorted(self.secrets.items(), key=lambda x: -len(x[0])):
                value = value.replace(raw, f"<secret:{name}>")
        return value

    def url(self, url):
        parsed = urlsplit(url)
        query = urlencode([(k, self.clean(v, k)) for k, v in parse_qsl(parsed.query)])
        return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, query, parsed.fragment))
