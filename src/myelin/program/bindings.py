import re
from html.parser import HTMLParser
from urllib.parse import quote, unquote, urlsplit

from myelin.schema import LiteralRef, MultiplyRef


class BindingError(ValueError):
    pass


def resolve(ref, inputs, variables, secrets, depth=0):
    if depth > 40:
        raise BindingError("reference depth exceeded")
    if isinstance(ref, LiteralRef):
        return ref.value
    if isinstance(ref, MultiplyRef):
        a = resolve(ref.left, inputs, variables, secrets, depth + 1)
        b = resolve(ref.right, inputs, variables, secrets, depth + 1)
        if type(a) is not int or type(b) is not int:
            raise BindingError("multiply_int requires integers")
        return a * b
    source = {"input": inputs, "variable": variables, "secret": secrets}[ref.kind]
    if ref.key not in source:
        raise BindingError(f"unavailable {ref.kind}: {ref.key}")
    return source[ref.key]


def json_path(value, expression):
    if not re.fullmatch(r"\$(?:\.[A-Za-z_][A-Za-z0-9_]*|\[-?[0-9]+\])*", expression):
        raise BindingError("unsupported JSON path")
    try:
        for match in re.finditer(r"\.([A-Za-z_][A-Za-z0-9_]*)|\[(-?[0-9]+)\]", expression):
            value = value[match[1]] if match[1] else value[int(match[2])]
        return value
    except (KeyError, IndexError, TypeError) as exc:
        raise BindingError(f"missing path: {expression}") from exc


class InputParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.values = {}

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "input" and "name" in attrs:
            self.values[attrs["name"]] = attrs.get("value", "")


def html_input(html, name):
    parser = InputParser()
    parser.feed(html)
    if name not in parser.values:
        raise BindingError(f"missing HTML input: {name}")
    return parser.values[name]


def bound_url(template, params):
    for key, value in params.items():
        template = template.replace("{" + key + "}", quote(str(value), safe=""))
    if "{" in template or "}" in template:
        raise BindingError("unresolved URL parameter")
    return template


def allow_url(url, allowed_origins):
    parsed = urlsplit(url)
    origin = f"{parsed.scheme}://{parsed.netloc}"
    decoded = parsed.path
    for _ in range(4):
        decoded = unquote(decoded)
    if (
        origin not in allowed_origins
        or parsed.username
        or parsed.password
        or "\\" in decoded
        or any(part.startswith("__") or part == ".." for part in decoded.split("/"))
    ):
        raise BindingError("URL outside permitted application surface")
    return url
