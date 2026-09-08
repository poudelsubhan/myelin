import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

SENSITIVE = re.compile(
    r"^(password|csrf|authorization|cookie|set-cookie|token|access_token|refresh_token|confirmation_token|"
    r"api_key|dsc|signature|secret|x-csrf-token|x-myelin-demo-token)$",
    re.I,
)


class Sanitizer:
    def __init__(self):
        self.secrets = {}
        self.sensitive_keys = set()

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
            key == "key"
            and isinstance(value, dict)
            and set(value) == {"kind", "value"}
            and value["kind"] == "literal"
            and value["value"]
            in (
                "Enter",
                "Escape",
                "Tab",
                "Home",
                "End",
                "ArrowUp",
                "ArrowDown",
                "ArrowLeft",
                "ArrowRight",
            )
        ):
            return dict(value)
        if (
            isinstance(value, dict)
            and set(value) == {"kind", "key"}
            and value.get("kind") in ("secret", "input", "variable")
            and isinstance(value.get("key"), str)
        ):
            # A typed binding contains a reference name, not the credential value.
            return dict(value)
        if (
            key.lower() == "authorization"
            and isinstance(value, str)
            and value.startswith("Bearer ")
        ):
            return "Bearer " + self.register(value[7:])
        if SENSITIVE.fullmatch(key) or key.lower() in self.sensitive_keys:
            return self.register(value)
        if isinstance(value, dict):
            return {k: self.clean(v, k) for k, v in value.items()}
        if isinstance(value, list):
            return [self.clean(v) for v in value]
        if isinstance(value, str):
            if value.startswith(("https://", "http://")):
                value = self.url(value)
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
        return urlunsplit((parsed.scheme, parsed.netloc.rsplit("@", 1)[-1], parsed.path, query, ""))
