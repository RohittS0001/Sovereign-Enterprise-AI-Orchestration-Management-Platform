"""Security regressions in the connector layer: host checks, redirects, response limits,
cloud project scoping, document ACLs and complete pagination."""

import gzip
import json

import httpx
import pytest

from backend.app.core.errors import IntegrationError
from backend.app.integrations.common.http import HttpIntegrationClient
from backend.app.integrations.connectors.base import checked_url, host_matches
from backend.app.integrations.connectors.calendar_meetings import ZoomService, _allowed_host
from backend.app.integrations.connectors.cloud import (
    AwsCloudService,
    AzureCloudService,
    GcpCloudService,
)
from backend.app.integrations.connectors.docs import (
    GoogleDriveService,
    NotionService,
    SharePointService,
    drive_restricted,
    sharepoint_restricted,
)
from tests.support.connectors import MockApi

# ------------------------------------------------------------------ 1. hostname validation


@pytest.mark.parametrize(
    "url",
    [
        "https://zoom.us/rec/download/abc",
        "https://us02web.zoom.us/rec/download/abc",
        "https://ZOOM.US/rec",
        "https://zoom.us:443/rec",
    ],
)
def test_vendor_hosts_and_real_subdomains_are_allowed(url: str) -> None:
    assert _allowed_host(url, ("zoom.us",)) == url


@pytest.mark.parametrize(
    "url",
    [
        "https://evilzoom.us/rec",  # lookalike: endswith("zoom.us") but another domain
        "https://zoom.us.evil.com/rec",  # vendor name as a subdomain of an attacker
        "https://notzoom.us/rec",
        "http://zoom.us/rec",  # plain HTTP
        "https://zoom.us:8443/rec",  # unexpected port
        "https://user:pw@zoom.us/rec",  # credentials in the URL
        "https://zoom.us@evil.com/rec",  # userinfo trick: the host is evil.com
        "https://127.0.0.1/rec",
        "https:///rec",
        "https://zoom..us/rec",
        "file:///etc/passwd",
        "https://[::1]/rec",
        "https://zoom.us:99999/rec",  # malformed port
    ],
)
def test_lookalike_and_malicious_urls_are_refused(url: str) -> None:
    with pytest.raises(ValueError):
        _allowed_host(url, ("zoom.us",))


def test_host_matching_uses_label_boundaries() -> None:
    assert host_matches("graph.microsoft.com", ("graph.microsoft.com",))
    assert not host_matches("evilgraph.microsoft.com", ("graph.microsoft.com",))
    assert not host_matches("graph.microsoft.com.evil.io", ("graph.microsoft.com",))
    assert not host_matches(None, ("zoom.us",)) and not host_matches("", ("zoom.us",))
    with pytest.raises(ValueError):
        checked_url("https://evil.example/x", ("example.com",))


# ------------------------------------------------------------------ 2. redirects


def redirecting_api(location_for: dict[str, str], seen: list[httpx.Request]) -> MockApi:
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        target = location_for.get(f"{request.url.host}{request.url.path}")
        if target:
            return httpx.Response(302, headers={"location": target})
        return httpx.Response(200, text="WEBVTT\n\n00:00.000 --> 00:01.000\nhello")

    return handler  # type: ignore[return-value]


async def zoom_with(handler, *, headers=None):
    from backend.app.integrations.connectors.base import ConnectorClient

    client = ConnectorClient(
        "Zoom",
        base_url="https://api.zoom.us",
        timeout=5,
        headers=headers or {"X-Api-Key": "static-secret"},  # noqa: S105 - test value
        transport=httpx.MockTransport(handler),
    )
    return client


async def test_cross_host_redirect_is_refused_before_any_request_is_sent() -> None:
    seen: list[httpx.Request] = []
    handler = redirecting_api({"zoom.us/rec/1": "https://attacker.example/steal"}, seen)
    client = await zoom_with(handler)

    with pytest.raises(IntegrationError, match="untrusted host"):
        await client.get_text("https://zoom.us/rec/1", redirect_hosts=("zoom.us",))

    assert [r.url.host for r in seen] == ["zoom.us"]  # attacker.example was never contacted
    await client.close()


async def test_redirect_chain_is_validated_hop_by_hop() -> None:
    seen: list[httpx.Request] = []
    handler = redirecting_api(
        {
            "zoom.us/rec/1": "https://ssrweb.zoom.us/a",  # allowed subdomain
            "ssrweb.zoom.us/a": "https://cdn.evilzoom.us/b",  # lookalike on the 2nd hop
        },
        seen,
    )
    client = await zoom_with(handler)

    with pytest.raises(IntegrationError, match="untrusted host"):
        await client.get_text("https://zoom.us/rec/1", redirect_hosts=("zoom.us",))

    assert [r.url.host for r in seen] == ["zoom.us", "ssrweb.zoom.us"]
    await client.close()


async def test_credentials_are_only_sent_to_the_first_origin() -> None:
    seen: list[httpx.Request] = []
    handler = redirecting_api({"zoom.us/rec/1": "https://ssrweb.zoom.us/file.vtt"}, seen)
    client = await zoom_with(handler)

    text = await client.get_text("https://zoom.us/rec/1", redirect_hosts=("zoom.us",))

    first, second = seen
    assert "hello" in text
    assert first.headers.get("x-api-key") == "static-secret"
    assert "x-api-key" not in second.headers and "authorization" not in second.headers
    await client.close()


async def test_redirect_to_plain_http_or_too_many_hops_is_refused() -> None:
    seen: list[httpx.Request] = []
    client = await zoom_with(redirecting_api({"zoom.us/rec/1": "http://zoom.us/rec/2"}, seen))
    with pytest.raises(IntegrationError):
        await client.get_text("https://zoom.us/rec/1", redirect_hosts=("zoom.us",))
    await client.close()

    loop = {f"zoom.us/r/{i}": f"https://zoom.us/r/{i + 1}" for i in range(10)}
    client = await zoom_with(redirecting_api(loop, []))
    with pytest.raises(IntegrationError, match="too many redirects"):
        await client.get_text("https://zoom.us/r/0", redirect_hosts=("zoom.us",))
    await client.close()


async def test_zoom_transcript_from_a_lookalike_host_is_never_downloaded() -> None:
    api = MockApi(
        {
            "GET /v2/users/me/recordings": {
                "meetings": [
                    {
                        "uuid": "m1",
                        "topic": "Standup",
                        "recording_files": [
                            {
                                "file_type": "TRANSCRIPT",
                                "download_url": "https://evilzoom.us/rec/x",
                            }
                        ],
                    }
                ]
            }
        }
    )
    svc = ZoomService(api.factory("Zoom", "https://api.zoom.us"))
    from datetime import date

    with pytest.raises(ValueError):
        await svc.list_transcripts(date(2026, 11, 1), date(2026, 11, 10))
    assert all(r.url.host != "evilzoom.us" for r in api.requests)


# ------------------------------------------------------------------ 19. response size limits


class Small(HttpIntegrationClient):
    display_name = "Small"
    integration = "small"


async def test_oversized_json_response_is_stopped_while_streaming() -> None:
    chunks_sent = []

    async def stream():
        for _ in range(100):
            chunks_sent.append(1)
            yield b"x" * 1024

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=stream())  # chunked: no Content-Length

    client = Small(
        base_url="https://api.example.com",
        timeout=5,
        transport=httpx.MockTransport(handler),
        max_response_bytes=10_000,
    )
    with pytest.raises(IntegrationError, match="larger than 10000 bytes"):
        await client.get("/big")
    assert len(chunks_sent) < 100  # reading stopped early, the body was not fully downloaded
    await client.close()


async def test_declared_length_over_the_limit_is_refused_and_gzip_bombs_count_decoded() -> None:
    def declared(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"content-length": "999999999"}, content=b"{}")

    client = Small(
        base_url="https://a.example.com",
        timeout=5,
        transport=httpx.MockTransport(declared),
        max_response_bytes=1000,
    )
    with pytest.raises(IntegrationError):
        await client.get("/x")
    await client.close()

    bomb = gzip.compress(b"0" * 200_000)  # ~200 bytes on the wire, 200 kB decoded

    def gz(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"content-encoding": "gzip"}, content=bomb)

    client = Small(
        base_url="https://a.example.com",
        timeout=5,
        transport=httpx.MockTransport(gz),
        max_response_bytes=50_000,
    )
    with pytest.raises(IntegrationError):
        await client.get("/x")
    await client.close()


async def test_responses_under_the_limit_still_parse_including_gzip() -> None:
    body = json.dumps({"ok": True}).encode()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, headers={"content-encoding": "gzip"}, content=gzip.compress(body)
        )

    client = Small(
        base_url="https://a.example.com", timeout=5, transport=httpx.MockTransport(handler)
    )
    assert await client.get("/x") == {"ok": True}
    await client.close()


async def test_downloads_are_size_capped_while_streaming() -> None:
    from backend.app.integrations.connectors.base import ConnectorClient

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"x" * 5000)

    client = ConnectorClient(
        "Docs",
        base_url="https://graph.microsoft.com",
        timeout=5,
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(IntegrationError, match="larger than"):
        await client.get_bytes("/file", max_bytes=1000)
    assert len(await client.get_bytes("/file", max_bytes=10_000)) == 5000
    await client.close()


# ------------------------------------------------------------------ 5. cloud project scoping


def azure_api(calls: list[str]) -> MockApi:
    def record(request: httpx.Request):
        calls.append(request.url.path)
        if "AlertsManagement" in request.url.path:
            return {
                "value": [
                    {
                        "id": "al-other",
                        "name": "Another project's alert",
                        "properties": {"essentials": {"targetResource": "/subscriptions/s/x"}},
                    }
                ]
            }
        return {
            "data": [{"id": "f1", "title": "Other finding", "resourceId": "/subscriptions/s/x"}]
        }

    return MockApi(
        {
            "GET /subscriptions/sub1/providers/Microsoft.AlertsManagement/alerts": record,
            "POST /providers/Microsoft.ResourceGraph/resources": record,
        }
    )


@pytest.mark.parametrize("ids", [set(), None])
async def test_azure_empty_project_scope_returns_nothing(ids) -> None:
    calls: list[str] = []
    svc = AzureCloudService(
        azure_api(calls).factory("Azure", "https://management.azure.com"), "sub1", "project"
    )

    assert await svc.alerts(["PHX"], ids) == []
    assert await svc.findings(["PHX"], ids) == []
    assert calls == []  # subscription-wide data is not even fetched


async def test_azure_scope_keeps_only_the_projects_resources() -> None:
    calls: list[str] = []
    svc = AzureCloudService(
        azure_api(calls).factory("Azure", "https://management.azure.com"), "sub1", "project"
    )

    unrelated = {"/subscriptions/sub1/resourcegroups/rg/providers/x/vm-phx"}
    assert await svc.alerts(["PHX"], unrelated) == []  # another project's alert is dropped
    assert await svc.findings(["PHX"], unrelated) == []
    assert await svc.alerts(["PHX"], {"/subscriptions/s/x"}) != []  # a matching id is kept


async def test_gcp_and_aws_empty_scopes_return_nothing() -> None:
    api = MockApi({})
    gcp = GcpCloudService(
        api.factory("GCP", "https://cloudasset.googleapis.com"), ["acme-web"], "project"
    )
    assert await gcp.findings(["phx"], set()) == [] and await gcp.alerts([], None) == []
    aws = AwsCloudService.__new__(AwsCloudService)  # no client needed: nothing may be called
    aws.regions = ["us-east-1"]
    assert await aws.alerts([], None) == [] and await aws.findings([], None) == []
    assert api.requests == []


# ------------------------------------------------------------------ 3. document ACLs


def test_sharepoint_permissions_map_to_visibility() -> None:
    assert sharepoint_restricted([]) is True  # nothing proves org-wide access
    assert sharepoint_restricted([{"grantedToV2": {"user": {"displayName": "Priya N"}}}]) is True
    assert sharepoint_restricted([{"link": {"scope": "users"}}]) is True
    assert sharepoint_restricted([{"link": {"scope": "organization"}}]) is False
    assert (
        sharepoint_restricted(
            [{"grantedToV2": {"siteUser": {"displayName": "Everyone except external users"}}}]
        )
        is False
    )


def test_drive_permissions_map_to_visibility_and_unknown_stays_unknown() -> None:
    assert drive_restricted([{"type": "user", "role": "reader"}]) is True
    assert drive_restricted([{"type": "domain", "domain": "acme.com"}]) is False
    assert drive_restricted([{"type": "anyone"}]) is False
    assert drive_restricted(None) is None  # not visible → the sync fails closed


async def test_notion_documents_have_unknown_acl() -> None:
    api = MockApi(
        {
            "POST /v1/search": {"results": [{"id": "pg1", "properties": {}}], "has_more": False},
            "GET /v1/blocks/pg1/children": {"results": [], "has_more": False},
        }
    )
    [doc] = await NotionService(api.factory("Notion", "https://api.notion.com")).list_documents()
    assert doc.restricted is None


async def test_sharepoint_permission_lookup_failure_fails_closed() -> None:
    api = MockApi(
        {
            "GET /v1.0/sites/s1/drive/root/children": {
                "value": [{"id": "f1", "name": "Plan.md", "file": {}}]
            },
            "GET /v1.0/sites/s1/drive/items/f1/content": "# Plan",
            "GET /v1.0/sites/s1/drive/items/f1/permissions": httpx.Response(403, json={}),
        }
    )
    [doc] = await SharePointService(
        api.factory("SP", "https://graph.microsoft.com/v1.0"), "s1"
    ).list_documents()
    assert doc.restricted is True


# ------------------------------------------------------------------ 10 / 11. pagination


async def test_sharepoint_follows_next_links_past_200_files() -> None:
    def page(request: httpx.Request):
        token = request.url.params.get("$skiptoken")
        start = int(token or 0)
        items = [
            {"id": f"f{i}", "name": f"Doc{i}.md", "file": {}} for i in range(start, start + 200)
        ]
        nxt = start + 200
        body: dict = {"value": items}
        if nxt < 450:
            body["@odata.nextLink"] = (
                f"https://graph.microsoft.com/v1.0/sites/s1/drive/root/children?$skiptoken={nxt}"
            )
        else:
            body["value"] = items[: 450 - start]
        return body

    routes = {"GET /v1.0/sites/s1/drive/root/children": page}

    def content_or_perms(request: httpx.Request):
        if request.url.path.endswith("/permissions"):
            return {"value": [{"link": {"scope": "organization"}}]}
        return "text"

    api = MockApi(routes)
    api.routes.update({f"GET /v1.0/sites/s1/drive/items/f{i}/content": "text" for i in range(450)})
    api.routes.update(
        {f"GET /v1.0/sites/s1/drive/items/f{i}/permissions": content_or_perms for i in range(450)}
    )
    svc = SharePointService(api.factory("SP", "https://graph.microsoft.com/v1.0"), "s1")

    docs = await svc.list_documents(limit=1000)

    assert len(docs) == 450 and docs.complete is True
    assert len({d.id for d in docs}) == 450 and all(d.restricted is False for d in docs)
    capped = await svc.list_documents(limit=300)
    assert len(capped) == 300 and capped.complete is False


async def test_sharepoint_refuses_a_next_link_to_another_host() -> None:
    api = MockApi(
        {
            "GET /v1.0/sites/s1/drive/root/children": {
                "value": [],
                "@odata.nextLink": "https://evil.example/v1.0/steal",
            }
        }
    )
    with pytest.raises(ValueError):
        await SharePointService(
            api.factory("SP", "https://graph.microsoft.com/v1.0"), "s1"
        ).list_documents()


async def test_google_drive_follows_page_tokens_past_100_files() -> None:
    def files(request: httpx.Request):
        token = request.url.params.get("pageToken")
        start = int(token or 0)
        end = min(start + 100, 250)
        body = {
            "files": [
                {"id": f"g{i:04d}", "name": f"N{i}.md", "mimeType": "text/markdown",
                 "permissions": [{"type": "user"}]}
                for i in range(start, end)
            ]
        }  # fmt: skip
        if end < 250:
            body["nextPageToken"] = str(end)
        return body

    api = MockApi({"GET /drive/v3/files": files})
    api.routes.update({f"GET /drive/v3/files/g{i:04d}": "text" for i in range(250)})
    svc = GoogleDriveService(api.factory("Drive", "https://www.googleapis.com"), "1AbCdEfGhIjKlMn")

    docs = await svc.list_documents(limit=1000)

    assert len(docs) == 250 and docs.complete is True
    assert all(d.restricted is True for d in docs)
    assert "permissions(" in api.requests[0].url.params["fields"]
    capped = await svc.list_documents(limit=120)
    assert len(capped) == 120 and capped.complete is False
