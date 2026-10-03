import time

import httpx

from backend import feed


async def test_langchain_canonical_articles_are_live_cached_and_filtered(monkeypatch):
    source = next(source for source in feed.SOURCES if source["id"] == "langchain")
    monkeypatch.setattr(feed, "_cache", {})
    monkeypatch.setattr(feed, "_failed_at", {})
    client_class = httpx.AsyncClient
    requests = []

    def handler(request):
        requests.append(str(request.url))
        return httpx.Response(
            200,
            text="""<rss><channel>
            <item><title>Canonical article</title>
            <link>https://www.langchain.com/blog/fixture</link>
            <pubDate>Thu, 01 Oct 2026 12:00:00 GMT</pubDate></item>
            <item><title>Deceptive host</title>
            <link>https://www.langchain.com.external.test/blog/fixture</link></item>
            <item><title>Authenticated URL</title>
            <link>https://test-only@www.langchain.com/blog/fixture</link></item>
            </channel></rss>""",
        )

    monkeypatch.setattr(
        feed.httpx,
        "AsyncClient",
        lambda **options: client_class(transport=httpx.MockTransport(handler), **options),
    )
    items, state = await feed.fetch_source(source)
    assert state["status"] == "live" and len(items) == 1
    assert items[0]["url"] == "https://www.langchain.com/blog/fixture"
    assert items[0]["source"] == "LangChain" and items[0]["track"] == "agent"
    assert items[0]["published"] == "2026-10-01T12:00:00+00:00"
    cached, state = await feed.fetch_source(source)
    assert cached == items and state["status"] == "cached"
    assert requests == [source["url"]]


async def test_stale_feed_failure_preserves_items_without_claiming_source_available(monkeypatch):
    source = feed.SOURCES[0]
    previous = [{"id": "saved-article", "title": "Old article"}]
    monkeypatch.setattr(feed, "_cache", {source["id"]: (time.monotonic() - 301, previous)})
    monkeypatch.setattr(feed, "_failed_at", {})
    client_class = httpx.AsyncClient

    def make_client(**options):
        assert options["follow_redirects"] is False
        return client_class(transport=httpx.MockTransport(lambda _: httpx.Response(503)), **options)

    monkeypatch.setattr(feed.httpx, "AsyncClient", make_client)
    items, state = await feed.fetch_source(source)
    assert items == previous
    assert state["status"] == "unavailable" and state["cached"] is True


async def test_redirect_is_not_followed_and_no_cache_is_invented(monkeypatch):
    source = feed.SOURCES[0]
    monkeypatch.setattr(feed, "_cache", {})
    monkeypatch.setattr(feed, "_failed_at", {})
    client_class = httpx.AsyncClient
    requests = []

    def handler(request):
        requests.append(str(request.url))
        return httpx.Response(301, headers={"Location": "https://external.test/secret"})

    monkeypatch.setattr(
        feed.httpx,
        "AsyncClient",
        lambda **options: client_class(transport=httpx.MockTransport(handler), **options),
    )
    items, state = await feed.fetch_source(source)
    assert requests == [source["url"]] and items == []
    assert state["status"] == "unavailable" and state["cached"] is False


async def test_failed_source_is_cooled_down_then_can_recover(monkeypatch):
    source = feed.SOURCES[0]
    monkeypatch.setattr(feed, "_cache", {})
    monkeypatch.setattr(feed, "_failed_at", {})
    client_class = httpx.AsyncClient
    requests = []

    def handler(request):
        requests.append(str(request.url))
        if len(requests) == 1:
            return httpx.Response(503)
        return httpx.Response(
            200,
            text="<rss><channel><item><title>Restored</title><link>https://openai.com/news/restored</link></item></channel></rss>",
        )

    monkeypatch.setattr(
        feed.httpx,
        "AsyncClient",
        lambda **options: client_class(transport=httpx.MockTransport(handler), **options),
    )
    for _ in range(2):
        items, state = await feed.fetch_source(source)
        assert not items and state["status"] == "unavailable"
    assert len(requests) == 1
    feed._failed_at[source["id"]] = time.monotonic() - 61
    items, state = await feed.fetch_source(source)
    assert items[0]["title"] == "Restored" and state["status"] == "live"
    assert source["id"] not in feed._failed_at
    _, state = await feed.fetch_source(source)
    assert state["status"] == "cached" and len(requests) == 2
