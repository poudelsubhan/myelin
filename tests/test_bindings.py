import pytest

from myelin.program.bindings import (
    BindingError,
    allow_url,
    bound_url,
    html_input,
    json_path,
    resolve,
)
from myelin.schema import LiteralRef, MultiplyRef, NamedRef


def test_typed_cents_and_quote_bindings():
    ref = MultiplyRef(
        left=NamedRef(kind="input", key="quantity"), right=NamedRef(kind="input", key="price")
    )
    assert resolve(ref, {"quantity": 3, "price": 1999}, {}, {}) == 5997
    with pytest.raises(BindingError):
        resolve(ref, {"quantity": True, "price": 1999}, {}, {})
    assert resolve(LiteralRef(value='"; import os'), {}, {}, {}) == '"; import os'
    with pytest.raises(BindingError):
        resolve(NamedRef(kind="variable", key="missing"), {}, {}, {})


def test_paths_extraction_and_segment_encoding():
    assert json_path({"a": [{"id": "fresh"}]}, "$.a[-1].id") == "fresh"
    with pytest.raises(BindingError):
        json_path({}, "$..x")
    assert html_input('<input name="csrf" value="fresh-token">', "csrf") == "fresh-token"
    assert bound_url("https://a.test/{id}", {"id": "a/b?x"}) == "https://a.test/a%2Fb%3Fx"


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost:8101/__state",
        "http://other.test/",
        "http://localhost:8101/%255f%255freset",
        "http://localhost:8101/a/../__chaos",
        "http://user@localhost:8101/",
    ],
)
def test_forbidden_urls(url):
    with pytest.raises(BindingError):
        allow_url(url, {"http://localhost:8101"})
