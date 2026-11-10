"""HR (leave) and finance (budget vs actuals) connectors.

HR: BambooHR, Zoho People, Darwinbox and Workday (a RaaS custom report), alongside the
core Keka integration. Every HR service returns ``LeaveRecord``s for a date range; only
leave fields are kept (no contact or salary data).

Finance: Zoho Books (projects + expenses), Tally Prime (cost-centre balances over its
XML HTTP interface) and SAP (an OData entity set exposing budget and actuals). Every
finance service returns ``BudgetLine``s.

Darwinbox and Workday report layouts vary by tenant: the parsers accept the common field
names, and ``python -m scripts.connections --check`` verifies a live connection.
"""

import re
from datetime import date, datetime
from typing import Any
from urllib.parse import urlsplit
from xml.etree import ElementTree  # noqa: S405 - see _parse_tally (DOCTYPE rejected)
from xml.sax.saxutils import escape

from backend.app.core.config import Settings
from backend.app.integrations.connectors.auth import zoho_token
from backend.app.integrations.connectors.base import (
    BudgetLine,
    ConnectorClient,
    ConnectorService,
    LeaveRecord,
    to_date,
)
from backend.app.integrations.connectors.spec import ConnectorSpec


def _first(row: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if row.get(key) not in (None, ""):
            return row[key]
    return None


def _flex_date(value: Any) -> date | None:
    """ISO, dd-MM-yyyy, dd-MMM-yyyy or dd/MM/yyyy (HR systems disagree)."""
    if value in (None, ""):
        return None
    text = str(value).strip()
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d-%b-%Y", "%d/%m/%Y", "%m/%d/%Y"):
        try:
            return datetime.strptime(text[:11].strip(), fmt).date()
        except ValueError:
            continue
    return to_date(text)


def _overlaps(record: LeaveRecord, start: date, end: date) -> bool:
    return record.start <= end and record.end >= start


def _leave(source: str, row: dict[str, Any], id_keys: tuple[str, ...], name_keys: tuple[str, ...],
           start_keys: tuple[str, ...], end_keys: tuple[str, ...], type_keys: tuple[str, ...],
           status_keys: tuple[str, ...]) -> LeaveRecord | None:  # fmt: skip
    start, end = _flex_date(_first(row, *start_keys)), _flex_date(_first(row, *end_keys))
    employee = _first(row, *id_keys)
    if not (start and end and employee):
        return None
    return LeaveRecord(source=source, employee_id=str(employee), name=_first(row, *name_keys),
                       start=start, end=end, type=_first(row, *type_keys),
                       status=_first(row, *status_keys))  # fmt: skip


REJECTED = {"rejected", "cancelled", "canceled", "declined", "denied", "withdrawn"}


def _keep(
    records: list[LeaveRecord], start: date, end: date, ids: list[str] | None
) -> list[LeaveRecord]:
    wanted = {i.lower() for i in ids or []}
    return [r for r in records
            if _overlaps(r, start, end) and (r.status or "").lower() not in REJECTED
            and (not wanted or r.employee_id.lower() in wanted)]  # fmt: skip


# ------------------------------------------------------------------ BambooHR


class BambooHRService(ConnectorService):
    source = "bamboohr"

    async def list_leave(
        self, start: date, end: date, employee_ids: list[str] | None = None
    ) -> list[LeaveRecord]:
        rows = await self.client.get(
            "/time_off/whos_out", {"start": start.isoformat(), "end": end.isoformat()}
        )
        records = [
            r
            for row in rows
            if row.get("type") == "timeOff"
            and (
                r := _leave(
                    self.source,
                    row,
                    ("employeeId",),
                    ("name",),
                    ("start",),
                    ("end",),
                    ("type",),
                    ("status",),
                )
            )
        ]
        return _keep(records, start, end, employee_ids)

    async def health_check(self) -> dict[str, Any]:
        today = date.today().isoformat()
        rows = await self.client.get("/time_off/whos_out", {"start": today, "end": today})
        return {"out_today": len(rows)}


# ------------------------------------------------------------------ Zoho People


class ZohoPeopleService(ConnectorService):
    source = "zoho_people"

    async def list_leave(
        self, start: date, end: date, employee_ids: list[str] | None = None
    ) -> list[LeaveRecord]:
        body = await self.client.get(
            "/people/api/forms/leave/getRecords", {"sIndex": 1, "limit": 200}
        )
        response = body.get("response") or {}
        if response.get("status") not in (0, None):
            raise ValueError(f"Zoho People: {response.get('message') or 'request failed'}")
        rows = [entry for record in response.get("result") or [] for entries in record.values()
                for entry in entries]  # fmt: skip
        records = [r for row in rows if (r := _leave(
            self.source, row, ("Employee_ID", "EmployeeID"), ("Employee_ID.name", "Employee_Name"),
            ("From",), ("To",), ("Leavetype", "Leave_Type"), ("ApprovalStatus",)))]  # fmt: skip
        return _keep(records, start, end, employee_ids)

    async def health_check(self) -> dict[str, Any]:
        await self.client.get("/people/api/forms/leave/getRecords", {"sIndex": 1, "limit": 1})
        return {"ok": True}


# ------------------------------------------------------------------ Darwinbox


class DarwinboxService(ConnectorService):
    source = "darwinbox"

    def __init__(self, client_factory: Any, api_key: str) -> None:
        super().__init__(client_factory)
        self.api_key = api_key

    async def list_leave(
        self, start: date, end: date, employee_ids: list[str] | None = None
    ) -> list[LeaveRecord]:
        body = await self.client.post("/leavesactionapi/leaveActionTakenLeaves", {
            "api_key": self.api_key, "from": start.strftime("%d-%m-%Y"),
            "to": end.strftime("%d-%m-%Y"), "action": "2",  # 2 = approved
        })  # fmt: skip
        if str(body.get("status", "1")) not in {"1", "true", "True"}:
            raise ValueError(f"Darwinbox: {body.get('message') or 'request failed'}")
        rows = body.get("leave_data") or body.get("data") or []
        records = [r for row in rows if (r := _leave(
            self.source, row, ("employee_no", "employee_id"), ("employee_name", "full_name"),
            ("from_date", "start_date"), ("to_date", "end_date"), ("leave_name", "leave_type"),
            ("status",)))]  # fmt: skip
        return _keep(records, start, end, employee_ids)

    async def health_check(self) -> dict[str, Any]:
        today = date.today()
        return {"approved_today": len(await self.list_leave(today, today))}


# ------------------------------------------------------------------ Workday (RaaS report)


class WorkdayService(ConnectorService):
    source = "workday"

    def __init__(self, client_factory: Any, report_url: str) -> None:
        super().__init__(client_factory)
        self.report_url = report_url

    async def list_leave(
        self, start: date, end: date, employee_ids: list[str] | None = None
    ) -> list[LeaveRecord]:
        body = await self.client.get(
            self.report_url,
            {"format": "json", "Start_Date": start.isoformat(), "End_Date": end.isoformat()},
        )
        records = [r for row in body.get("Report_Entry") or [] if (r := _leave(
            self.source, row, ("Employee_ID", "Worker_ID"), ("Worker", "Employee_Name"),
            ("Start_Date", "Time_Off_Date", "Date"), ("End_Date", "Time_Off_Date", "Date"),
            ("Time_Off_Type", "Type"), ("Status", "Approval_Status")))]  # fmt: skip
        return _keep(records, start, end, employee_ids)

    async def health_check(self) -> dict[str, Any]:
        today = date.today()
        return {"entries_today": len(await self.list_leave(today, today))}


# ------------------------------------------------------------------ Zoho Books


class ZohoBooksService(ConnectorService):
    source = "zoho_books"

    def __init__(self, client_factory: Any, organization_id: str) -> None:
        super().__init__(client_factory)
        self.org = {"organization_id": organization_id}

    async def budget_lines(self, scope: list[str] | None = None) -> list[BudgetLine]:
        body = await self.client.get("/projects", self.org)
        wanted = {s.lower() for s in scope or []}
        lines = []
        for project in body.get("projects") or []:
            name = project.get("project_name", "")
            if wanted and name.lower() not in wanted:
                continue
            expenses = await self.client.get(
                "/expenses", {**self.org, "project_id": project.get("project_id")}
            )
            actual = sum(float(e.get("total") or 0) for e in expenses.get("expenses") or [])
            budget = project.get("budget_amount")
            lines.append(BudgetLine(source=self.source, project=name,
                                    budget=float(budget) if budget not in (None, "") else None,
                                    actual=round(actual, 2), currency=project.get("currency_code")))  # fmt: skip
        return lines

    async def health_check(self) -> dict[str, Any]:
        body = await self.client.get("/organizations")
        return {"organizations": len(body.get("organizations") or [])}


# ------------------------------------------------------------------ Tally Prime

_TALLY_REQUEST = """<ENVELOPE><HEADER><VERSION>1</VERSION><TALLYREQUEST>Export</TALLYREQUEST>
<TYPE>Collection</TYPE><ID>SovereignCostCentres</ID></HEADER><BODY><DESC><STATICVARIABLES>
<SVEXPORTFORMAT>$$SysName:XML</SVEXPORTFORMAT>{company}</STATICVARIABLES><TDL><TDLMESSAGE>
<COLLECTION NAME="SovereignCostCentres"><TYPE>CostCentre</TYPE><FETCH>Name, ClosingBalance</FETCH>
</COLLECTION></TDLMESSAGE></TDL></DESC></BODY></ENVELOPE>"""


def _parse_tally(xml: str) -> list[tuple[str, float]]:
    # Tally is a local, configured server; still refuse DTDs (entity expansion) and huge bodies.
    if "<!DOCTYPE" in xml.upper() or len(xml) > 5_000_000:
        raise ValueError("Unexpected Tally response")
    root = ElementTree.fromstring(xml)  # noqa: S314 - DOCTYPE rejected above
    rows = []
    for centre in root.iter("COSTCENTRE"):
        name = centre.get("NAME") or (centre.findtext("NAME") or "")
        balance = (centre.findtext("CLOSINGBALANCE") or "0").replace(",", "").strip() or "0"
        rows.append((name, abs(float(re.sub(r"[^0-9.\-]", "", balance) or 0))))
    return rows


class TallyService(ConnectorService):
    source = "tally"

    def __init__(self, client_factory: Any, company: str = "") -> None:
        super().__init__(client_factory)
        self.company = company

    async def _export(self) -> list[tuple[str, float]]:
        company = (f"<SVCURRENTCOMPANY>{escape(self.company)}</SVCURRENTCOMPANY>"
                   if self.company else "")  # fmt: skip
        response = await self.client._client.post(
            "/", content=_TALLY_REQUEST.format(company=company).encode(),
            headers={"Content-Type": "text/xml"})  # fmt: skip
        response.raise_for_status()
        return _parse_tally(response.text)

    async def budget_lines(self, scope: list[str] | None = None) -> list[BudgetLine]:
        wanted = {s.lower() for s in scope or []}
        return [BudgetLine(source=self.source, project=name, actual=balance)
                for name, balance in await self._export() if not wanted or name.lower() in wanted]  # fmt: skip

    async def health_check(self) -> dict[str, Any]:
        return {"cost_centres": len(await self._export())}


# ------------------------------------------------------------------ SAP (OData)

_ODATA_FIELD = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")


class SapService(ConnectorService):
    source = "sap"

    def __init__(self, client_factory: Any, entity_url: str, fields: dict[str, str]) -> None:
        super().__init__(client_factory)
        self.entity_url = entity_url
        for name in fields.values():
            if not _ODATA_FIELD.fullmatch(name):
                raise ValueError(f"Invalid SAP field name {name!r}")
        self.fields = fields

    async def budget_lines(self, scope: list[str] | None = None) -> list[BudgetLine]:
        f = self.fields
        params: dict[str, Any] = {
            "$format": "json",
            "$select": ",".join([f["project"], f["budget"], f["actual"], f["currency"]]),
        }
        if scope:
            values = " or ".join(
                f"{f['project']} eq '{s.replace(chr(39), chr(39) * 2)[:100]}'" for s in scope
            )
            params["$filter"] = values
        body = await self.client.get(self.entity_url, params)
        rows = (
            (body.get("d") or {}).get("results")
            if isinstance(body.get("d"), dict)
            else body.get("value")
        )
        return [BudgetLine(source=self.source, project=str(r.get(f["project"], "")),
                           budget=_num(r.get(f["budget"])), actual=_num(r.get(f["actual"])),
                           currency=r.get(f["currency"]))
                for r in rows or []]  # fmt: skip

    async def health_check(self) -> dict[str, Any]:
        body = await self.client.get(self.entity_url, {"$format": "json", "$top": 1})
        return {"ok": bool(body)}


def _num(value: Any) -> float | None:
    try:
        return float(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


# ------------------------------------------------------------------ demo twins


class DemoHR:
    def __init__(self, source: str, data: dict[str, Any]) -> None:
        self.source = source
        self.records = [LeaveRecord(source=source, **r) for r in data.get("leave", [])]

    async def list_leave(
        self, start: date, end: date, employee_ids: list[str] | None = None
    ) -> list[LeaveRecord]:
        return _keep(self.records, start, end, employee_ids)

    async def health_check(self) -> dict[str, str]:
        return {"mode": "demo"}

    async def close(self) -> None:
        return None


class DemoFinance:
    def __init__(self, source: str, data: dict[str, Any]) -> None:
        self.source = source
        self.lines = [BudgetLine(source=source, **r) for r in data.get("budget_lines", [])]

    async def budget_lines(self, scope: list[str] | None = None) -> list[BudgetLine]:
        wanted = {s.lower() for s in scope or []}
        return [line for line in self.lines if not wanted or line.project.lower() in wanted]

    async def health_check(self) -> dict[str, str]:
        return {"mode": "demo"}

    async def close(self) -> None:
        return None


def origin(url: str) -> str:
    return "{0.scheme}://{0.netloc}".format(urlsplit(url))


def path_of(url: str) -> str:
    return urlsplit(url).path or "/"


def _client(s: Settings, name: str, base_url: str, **kwargs: Any) -> Any:
    return lambda: ConnectorClient(
        name, base_url=base_url, timeout=s.connector_timeout_seconds, **kwargs
    )


def _zoho(s: Settings, name: str, base_url: str) -> Any:
    token = zoho_token(s)
    return _client(
        s,
        name,
        base_url,
        token_provider=token,
        auth_header=("Authorization", "Zoho-oauthtoken {token}"),
    )


HR_SPECS = (
    ConnectorSpec(
        "bamboohr",
        "BambooHR",
        "hr",
        required=("bamboohr_company", "bamboohr_api_key"),
        setup="BambooHR → your name → API Keys (a user who can see time off)",
        docs_url="https://documentation.bamboohr.com/reference/get-a-list-of-whos-out-1",
        build=lambda s: BambooHRService(
            _client(
                s,
                "BambooHR",
                f"https://api.bamboohr.com/api/gateway.php/{s.bamboohr_company}/v1",
                auth=(s.bamboohr_api_key.get_secret_value(), "x"),
            )
        ),
        demo=lambda data, today: DemoHR("bamboohr", data.get("hr", {}).get("bamboohr", {})),
    ),
    ConnectorSpec(
        "zoho_people",
        "Zoho People",
        "hr",
        required=("zoho_client_id", "zoho_client_secret", "zoho_refresh_token"),
        optional=("zoho_accounts_url", "zoho_people_url"),
        setup="api-console.zoho.com → Self Client → scope ZOHOPEOPLE.forms.READ → refresh token; "
        "regional accounts use their domain (e.g. accounts.zoho.in)",
        docs_url="https://www.zoho.com/people/api/overview.html",
        build=lambda s: ZohoPeopleService(_zoho(s, "Zoho People", s.zoho_people_url)),
        demo=lambda data, today: DemoHR("zoho_people", data.get("hr", {}).get("zoho_people", {})),
    ),
    ConnectorSpec(
        "darwinbox",
        "Darwinbox",
        "hr",
        required=("darwinbox_url", "darwinbox_username", "darwinbox_password", "darwinbox_api_key"),
        setup="Darwinbox admin → API settings (leave action API key + basic-auth user)",
        docs_url="https://darwinbox.com/",
        build=lambda s: DarwinboxService(
            _client(
                s,
                "Darwinbox",
                s.darwinbox_url,
                auth=(s.darwinbox_username, s.darwinbox_password.get_secret_value()),
            ),
            s.darwinbox_api_key.get_secret_value(),
        ),
        demo=lambda data, today: DemoHR("darwinbox", data.get("hr", {}).get("darwinbox", {})),
    ),
    ConnectorSpec(
        "workday",
        "Workday",
        "hr",
        required=("workday_leave_report_url", "workday_username", "workday_password"),
        setup="Create a custom report of time off (Employee_ID, Worker, Start_Date, End_Date, "
        "Time_Off_Type, Status) enabled as a web service; use an integration system user",
        docs_url="https://community.workday.com/",
        build=lambda s: WorkdayService(
            _client(
                s,
                "Workday",
                origin(s.workday_leave_report_url),
                auth=(s.workday_username, s.workday_password.get_secret_value()),
            ),
            path_of(s.workday_leave_report_url),
        ),
        demo=lambda data, today: DemoHR("workday", data.get("hr", {}).get("workday", {})),
    ),
)

FINANCE_SPECS = (
    ConnectorSpec(
        "zoho_books", "Zoho Books", "finance",
        required=("zoho_client_id", "zoho_client_secret", "zoho_refresh_token", "zoho_books_organization_id"),
        optional=("zoho_books_url",),
        setup="Same Zoho self client with scope ZohoBooks.projects.READ,ZohoBooks.expenses.READ; "
              "organization id from Settings → Organization profile",
        docs_url="https://www.zoho.com/books/api/v3/",
        build=lambda s: ZohoBooksService(_zoho(s, "Zoho Books", s.zoho_books_url), s.zoho_books_organization_id),
        demo=lambda data, today: DemoFinance("zoho_books", data.get("finance", {}).get("zoho_books", {})),
    ),
    ConnectorSpec(
        "tally", "Tally Prime", "finance", required=("tally_url",), optional=("tally_company",),
        setup="Tally Prime → F1 Help → Settings → Connectivity: enable the HTTP server (port 9000)",
        docs_url="https://help.tallysolutions.com/integration-with-tallyprime/",
        build=lambda s: TallyService(_client(s, "Tally", s.tally_url), s.tally_company),
        demo=lambda data, today: DemoFinance("tally", data.get("finance", {}).get("tally", {})),
    ),
    ConnectorSpec(
        "sap", "SAP S/4HANA (OData)", "finance", required=("sap_odata_url", "sap_username", "sap_password"),
        optional=("sap_project_field", "sap_budget_field", "sap_actual_field", "sap_currency_field"),
        setup="Expose a CDS view / OData service with project, budget, actual and currency; a "
              "communication user with read access; map the field names if they differ",
        docs_url="https://api.sap.com/",
        build=lambda s: SapService(_client(s, "SAP", origin(s.sap_odata_url),
                                           auth=(s.sap_username, s.sap_password.get_secret_value())),
                                   path_of(s.sap_odata_url),
                                   {"project": s.sap_project_field, "budget": s.sap_budget_field,
                                    "actual": s.sap_actual_field, "currency": s.sap_currency_field}),
        demo=lambda data, today: DemoFinance("sap", data.get("finance", {}).get("sap", {})),
    ),
)  # fmt: skip
