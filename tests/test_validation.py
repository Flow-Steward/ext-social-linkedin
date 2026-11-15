import pytest

from linkedin.validation import provider_post, validate_post


@pytest.mark.parametrize("kind", ["text", "image", "article"])
def test_supported_posts(kind):
    post = {"kind": kind, "text": "Hello"}
    if kind == "image":
        post["image_urn"] = "urn:li:image:asset_1"
    if kind == "article":
        post.update(source_url="https://example.org/release", title="Release")
    assert validate_post(post)["valid"] is True
    body = provider_post(post, "urn:li:person:abc")
    assert body["author"] == "urn:li:person:abc"
    assert body["distribution"]["feedDistribution"] == "MAIN_FEED"
    assert body["visibility"] == "PUBLIC"
    assert body["lifecycleState"] == "PUBLISHED"


@pytest.mark.parametrize("value", ["a" * 3000, "😀" * 1500])
def test_exact_utf16_boundary(value):
    assert validate_post({"kind": "text", "text": value})["valid"] is True
    assert validate_post({"kind": "text", "text": value + "x"})["valid"] is False


@pytest.mark.parametrize("text", ["", " \n\t", "bad\x00", "bad\x7f", "bad\ud800"])
def test_invalid_text(text):
    assert validate_post({"kind": "text", "text": text})["valid"] is False


@pytest.mark.parametrize(
    "post",
    [
        None,
        [],
        {},
        {"kind": "video", "text": "x"},
        {"kind": "text", "text": "x", "author": "urn:li:person:evil"},
        {"kind": "text", "text": "x", "image_urn": "urn:li:image:x"},
        {"kind": "image", "text": "x", "image_urn": "http://evil"},
        {"kind": "article", "text": "x", "source_url": "https://u:p@example.org", "title": "x"},
        {"kind": "article", "text": "x", "source_url": "file:///tmp/a", "title": "x"},
        {"kind": "article", "text": "x", "source_url": "https://[", "title": "x"},
        {"kind": "article", "text": "x", "source_url": "https://example.org", "title": " "},
        {"kind": "image", "text": "x", "image_urn": "urn:li:image:x", "alt_text": None},
    ],
)
def test_closed_union(post):
    result = validate_post(post)
    assert result["valid"] is False
    assert result["violations"]


def test_plain_text_is_losslessly_escaped():
    text = "Hi @[name](urn:li:person:evil) #tag *text* _foo_ ~bar~ <x> {a|b} \\\\"
    body = provider_post({"kind": "text", "text": text}, "urn:li:person:abc")
    escaped = body["commentary"]
    for char in r"|{}@[]()<>#\*_~":
        assert "\\" + char in escaped
    assert "\n" in provider_post({"kind": "text", "text": "a\nb"}, "urn:li:person:a")["commentary"]


@pytest.mark.parametrize("field,maximum", [("title", 200), ("description", 256)])
def test_article_limits_and_empty_optionals(field, maximum):
    post = {"kind": "article", "text": "x", "source_url": "https://example.org", "title": "a"}
    post[field] = "x" * maximum
    assert validate_post(post)["valid"] is True
    post[field] += "x"
    assert validate_post(post)["valid"] is False
    post.update(title="a", description="")
    assert "description" not in provider_post(post, "urn:li:person:a")["content"]["article"]
