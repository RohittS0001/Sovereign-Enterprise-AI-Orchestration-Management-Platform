"""KekaService request building, paging and validation (over httpx.MockTransport)."""

from datetime import date

import httpx
import pytest

from backend.app.integrations.keka.client import KekaClient
from backend.app.integrations.keka.service import KekaService

TOKEN = httpx.Response(200, json={"access_token": "tok", "expires_in": 3600})


def service(*responses: httpx.Response) -> tuple[KekaService, list[httpx.Request]]:
    queue, seen = [TOKEN, *responses], []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return queue.pop(0)

    client = KekaClient(
        "https://acme.keka.com/api/v1", "https://login.keka.com/connect/token",
        "cid", "secret", "key", 5, transport=httpx.MockTransport(handler),
    )  # fmt: skip
    return KekaService(client=client), seen


async def test_search_employees_pages_and_filters() -> None:
    svc, seen = service(
        httpx.Response(
            200, json={"data": [{"id": "e-1", "displayName": "Priya"}], "totalRecords": 7}
        )
    )

    result = await svc.search_employees("pri", employment_status="All", limit=5)

    assert result["total"] == 7 and result["employees"][0]["id"] == "e-1"
    params = seen[-1].url.params
    assert params["searchKey"] == "pri" and params["pageSize"] == "5"
    assert "employmentStatus" not in params  # "All" means no filter


async def test_leave_balances_and_attendance_accept_plain_lists() -> None:
    svc, seen = service(
        httpx.Response(200, json=[{"employeeId": "e-1", "balance": 4}]),
        httpx.Response(200, json={"data": [{"employeeId": "e-1", "status": "present"}]}),
    )

    balances = await svc.get_leave_balances(["e-1"])
    attendance = await svc.get_attendance(date(2026, 11, 2), date(2026, 11, 6), ["e-1"])

    assert balances == {"total": 1, "leave_balances": [{"employeeId": "e-1", "balance": 4}]}
    assert attendance["total"] == 1
    assert seen[-1].url.params["from"] == "2026-11-02"


async def test_get_employee_unwraps_data_envelope() -> None:
    svc, _ = service(httpx.Response(200, json={"data": {"id": "e-9", "displayName": "Ravi"}}))

    assert (await svc.get_employee("e-9"))["id"] == "e-9"


@pytest.mark.parametrize("bad", ["../hris", "e 1", "x" * 80])
async def test_employee_ids_are_validated_before_any_request(bad) -> None:
    svc, seen = service()

    with pytest.raises(ValueError):
        await svc.get_employee(bad)
    assert seen == []


async def test_ranges_are_validated() -> None:
    svc, seen = service()

    with pytest.raises(ValueError):
        await svc.get_leave_requests(date(2026, 3, 1), date(2026, 1, 1))
    assert seen == []


async def test_lazy_client_and_close() -> None:
    built = []

    def factory() -> KekaClient:
        built.append(1)
        return KekaClient(
            "https://acme.keka.com/api/v1", "https://login.keka.com/connect/token",
            "cid", "secret", "key", 5,
            transport=httpx.MockTransport(lambda r: httpx.Response(200, json={})),
        )  # fmt: skip

    svc = KekaService(client_factory=factory)
    await svc.close()  # nothing built yet
    assert built == []
    _ = svc.client
    await svc.close()
    assert built == [1]
