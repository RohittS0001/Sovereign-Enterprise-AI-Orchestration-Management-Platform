"""HR and finance connectors against mocked APIs; earned value; leave matched across systems."""

from datetime import date

import httpx
import pytest

from backend.app.core.config import Settings
from backend.app.domains.analytics.collector import SnapshotCollector
from backend.app.domains.analytics.evm import budget_factor, earned_value
from backend.app.domains.analytics.snapshot import IssueFacts, ProjectSnapshot
from backend.app.integrations.connectors.base import BudgetLine, ConnectorClient
from backend.app.integrations.connectors.hr_finance import (
    BambooHRService,
    DarwinboxService,
    SapService,
    TallyService,
    WorkdayService,
    ZohoBooksService,
    ZohoPeopleService,
    _parse_tally,
)
from backend.app.integrations.connectors.registry import spec
from backend.app.integrations.hub import build_integration_hub
from tests.support.analytics import phx_project
from tests.support.connectors import MockApi

NOV = (date(2026, 11, 9), date(2026, 11, 20))


async def test_bamboohr_keeps_time_off_only() -> None:
    api = MockApi({"GET /time_off/whos_out": [
        {"type": "timeOff", "employeeId": 7, "name": "Sara K", "start": "2026-11-10", "end": "2026-11-12"},
        {"type": "holiday", "name": "Diwali", "start": "2026-11-11", "end": "2026-11-11"},
    ]})  # fmt: skip
    svc = BambooHRService(api.factory("BambooHR", "https://api.bamboohr.com", auth=("key", "x")))

    [leave] = await svc.list_leave(*NOV)

    assert (leave.employee_id, leave.name, leave.start) == ("7", "Sara K", date(2026, 11, 10))
    assert api.last.url.params["start"] == "2026-11-09"


async def test_zoho_people_parses_its_record_envelope_and_skips_rejected() -> None:
    api = MockApi({"GET /people/api/forms/leave/getRecords": {"response": {"status": 0, "result": [
        {"1001": [{"Employee_ID": "Z-1", "From": "10-Nov-2026", "To": "11-Nov-2026",
                   "Leavetype": "Casual", "ApprovalStatus": "Approved"}]},
        {"1002": [{"Employee_ID": "Z-2", "From": "12-Nov-2026", "To": "12-Nov-2026",
                   "ApprovalStatus": "Rejected"}]},
        {"1003": [{"Employee_ID": "Z-3", "From": "01-Jan-2026", "To": "02-Jan-2026"}]},
    ]}}})  # fmt: skip
    svc = ZohoPeopleService(api.factory("Zoho People", "https://people.zoho.com"))

    assert [r.employee_id for r in await svc.list_leave(*NOV)] == ["Z-1"]


async def test_darwinbox_posts_the_api_key_and_dd_mm_yyyy_dates() -> None:
    api = MockApi({"POST /leavesactionapi/leaveActionTakenLeaves": {"status": 1, "leave_data": [
        {"employee_no": "D-9", "employee_name": "Meera S", "from_date": "13-11-2026",
         "to_date": "14-11-2026", "leave_name": "Earned"}]}})  # fmt: skip
    svc = DarwinboxService(
        api.factory("Darwinbox", "https://acme.darwinbox.in", auth=("u", "p")), "k-1"
    )

    [leave] = await svc.list_leave(*NOV)

    assert api.json_body() == {
        "api_key": "k-1",
        "from": "09-11-2026",
        "to": "20-11-2026",
        "action": "2",
    }
    assert (leave.employee_id, leave.end) == ("D-9", date(2026, 11, 14))


async def test_workday_report_is_called_at_its_exact_path() -> None:
    api = MockApi({"GET /ccx/service/customreport2/acme/isu/Time_Off": {"Report_Entry": [
        {"Employee_ID": "W-4", "Worker": "Dev R", "Start_Date": "2026-11-16", "End_Date": "2026-11-17",
         "Time_Off_Type": "Vacation", "Status": "Approved"}]}})  # fmt: skip
    url = "https://wd5-services1.myworkday.com/ccx/service/customreport2/acme/isu/Time_Off"
    settings = Settings(
        _env_file=None,
        environment="test",
        workday_leave_report_url=url,
        workday_username="isu",
        workday_password="pw",
    )  # noqa: S106
    svc = spec("workday").build(settings)
    svc._client_factory = lambda: ConnectorClient(  # same origin/path split, mocked transport
        "Workday", base_url="https://wd5-services1.myworkday.com", timeout=5,
        transport=httpx.MockTransport(api))  # fmt: skip

    [leave] = await svc.list_leave(*NOV)

    assert isinstance(svc, WorkdayService) and leave.name == "Dev R"
    assert api.last.url.params["format"] == "json"


async def test_zoho_books_sums_expenses_per_project() -> None:
    api = MockApi({
        "GET /books/v3/projects": {"projects": [
            {"project_id": "p1", "project_name": "Phoenix", "budget_amount": 1000, "currency_code": "INR"},
            {"project_id": "p2", "project_name": "Other"}]},
        "GET /books/v3/expenses": {"expenses": [{"total": 120.5}, {"total": 79.5}]},
    })  # fmt: skip
    svc = ZohoBooksService(api.factory("Zoho Books", "https://www.zohoapis.com/books/v3"), "org-1")

    [line] = await svc.budget_lines(scope=["phoenix"])

    assert (line.budget, line.actual, line.currency) == (1000.0, 200.0, "INR")
    assert (
        api.last.url.params["organization_id"] == "org-1"
        and api.last.url.params["project_id"] == "p1"
    )


def test_tally_xml_is_parsed_and_dtds_are_refused() -> None:
    xml = ("<ENVELOPE><COSTCENTRE NAME='Phoenix'><CLOSINGBALANCE>-1,25,000.00</CLOSINGBALANCE>"
           "</COSTCENTRE><COSTCENTRE NAME='Ops'><CLOSINGBALANCE></CLOSINGBALANCE></COSTCENTRE></ENVELOPE>")  # fmt: skip
    assert _parse_tally(xml) == [("Phoenix", 125000.0), ("Ops", 0.0)]
    with pytest.raises(ValueError):
        _parse_tally('<!DOCTYPE x [<!ENTITY a "aaaa">]><ENVELOPE>&a;</ENVELOPE>')


async def test_tally_posts_an_xml_export_request() -> None:
    api = MockApi(
        {
            "POST /": "<ENVELOPE><COSTCENTRE NAME='Phoenix'><CLOSINGBALANCE>500</CLOSINGBALANCE></COSTCENTRE></ENVELOPE>"
        }
    )
    svc = TallyService(api.factory("Tally", "http://localhost:9000"), company="Acme <Ltd>")

    [line] = await svc.budget_lines(scope=["phoenix"])

    body = api.last.content.decode()
    assert (
        "<TYPE>CostCentre</TYPE>" in body
        and "<SVCURRENTCOMPANY>Acme &lt;Ltd&gt;</SVCURRENTCOMPANY>" in body
    )
    assert (line.actual, line.budget) == (500.0, None)


async def test_sap_odata_filter_escapes_quotes_and_validates_field_names() -> None:
    api = MockApi({"GET /sap/opu/odata/sap/ZPROJ_SRV/Budgets": {"d": {"results": [
        {"Project": "PHX", "Budget": "1000.00", "Actual": "250", "Currency": "EUR"}]}}})  # fmt: skip
    fields = {"project": "Project", "budget": "Budget", "actual": "Actual", "currency": "Currency"}
    svc = SapService(
        api.factory("SAP", "https://sap.example.com"),
        "/sap/opu/odata/sap/ZPROJ_SRV/Budgets",
        fields,
    )

    [line] = await svc.budget_lines(scope=["O'Hara"])

    assert api.last.url.params["$filter"] == "Project eq 'O''Hara'"
    assert (line.budget, line.actual, line.currency) == (1000.0, 250.0, "EUR")
    with pytest.raises(ValueError):
        SapService(api.factory("SAP", "https://x"), "/e", {**fields, "budget": "Budget;drop"})


def _issue(key: str, points: float, done: bool) -> IssueFacts:
    category = "done" if done else "todo"
    return IssueFacts(key=key, summary=key, status=category, category=category, priority="Medium",
                      assignee=None, assignee_id=None, due=None, points=points, issue_type="Story")  # fmt: skip


def test_earned_value_and_budget_factor() -> None:
    snap = ProjectSnapshot(key="P", name="P", today=NOV[0],
                           issues=[_issue("P-1", 5, True), _issue("P-2", 5, False)],
                           finance=[BudgetLine(source="sap", project="P", budget=100_000,
                                               actual=80_000, currency="EUR")])  # fmt: skip
    value = earned_value(snap)

    assert (value.percent_complete, value.earned_value, value.cpi) == (0.5, 50_000, 0.62)
    assert value.estimate_at_completion == 160_000 and value.variance_at_completion == -60_000
    factor = budget_factor(value)
    assert factor.score == 10 and "Forecast overrun 60,000 EUR" in factor.evidence[1]
    unknown = earned_value(ProjectSnapshot(key="Q", name="Q", today=NOV[0],
                                           finance=[BudgetLine(source="tally", project="Q", actual=5)]))  # fmt: skip
    assert unknown.cpi is None and "No budget" in unknown.note


async def test_leave_from_another_hr_system_reduces_capacity_by_name_or_hr_id() -> None:
    settings = Settings(_env_file=None, demo_mode=True)
    hub = build_integration_hub(settings, demo_today=NOV[0])

    class FakeHR:
        async def list_leave(self, start, end, ids=None):
            from backend.app.integrations.connectors.base import LeaveRecord

            return [LeaveRecord(source="bamboohr", employee_id="x-1", name="Meera S",
                                start=NOV[0], end=date(2026, 11, 13))]  # fmt: skip

    hub.connectors = {"bamboohr": FakeHR()}
    snap = await SnapshotCollector(hub, settings).collect(phx_project(), today=NOV[0])

    meera = [lv for lv in snap.leave if lv.source == "bamboohr"]
    assert meera and meera[0].employee_id == "e-005"  # mapped to Meera's member id
