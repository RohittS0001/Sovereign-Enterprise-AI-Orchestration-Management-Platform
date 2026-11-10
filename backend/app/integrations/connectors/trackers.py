"""Issue trackers & code hosting beyond Jira/GitHub, behind one WorkItem/PullRequest model.

Linear (GraphQL), GitLab, Azure DevOps (WIQL), Asana, ClickUp and Bitbucket. Every
service implements ``search_work_items`` / ``get_work_item`` / ``list_pull_requests`` /
``health_check``; identifiers are validated before they reach a URL or query.
"""

from typing import Any
from urllib.parse import quote, urlsplit

from backend.app.core.config import Settings
from backend.app.integrations.connectors.base import (
    Category3,
    ConnectorClient,
    ConnectorService,
    PullRequest,
    WorkItem,
    category_of,
    safe_id,
    to_date,
)
from backend.app.integrations.connectors.spec import ConnectorSpec


def _match(item: WorkItem, query: str | None, status: Category3 | None) -> bool:
    if status and item.category != status:
        return False
    return not query or query.lower() in f"{item.key} {item.title}".lower()


def _allowed(value: str, allowed: list[str], what: str) -> str:
    value = safe_id(value, what)
    if allowed and value.lower() not in {a.lower() for a in allowed}:
        raise ValueError(f"{what} {value!r} is not in the allowed list")
    return value


# ------------------------------------------------------------------ Linear

_LINEAR_STATES = {"todo": ["backlog", "unstarted", "triage"], "in_progress": ["started"],
                  "done": ["completed", "canceled"]}  # fmt: skip
_LINEAR_FIELDS = (
    "identifier title url updatedAt dueDate estimate priorityLabel "
    "state { name type } assignee { name } team { key }"
)


class LinearService(ConnectorService):
    source = "linear"

    def __init__(
        self, client_factory: Any, team_keys: list[str] | None = None, path: str = "/graphql"
    ) -> None:
        super().__init__(client_factory)
        self.team_keys = team_keys or []
        self.path = path  # exact endpoint path (no trailing slash)

    async def _gql(self, query: str, variables: dict[str, Any] | None = None) -> dict[str, Any]:
        body = await self.client.post(self.path, {"query": query, "variables": variables or {}})
        if body.get("errors"):
            raise ValueError(f"Linear: {body['errors'][0].get('message', 'query failed')}")
        return body.get("data") or {}

    def _item(self, node: dict[str, Any]) -> WorkItem:
        state = node.get("state") or {}
        kind = state.get("type") or ""
        category = next((c for c, types in _LINEAR_STATES.items() if kind in types), "todo")
        return WorkItem(
            source=self.source, key=node.get("identifier", ""), title=node.get("title", ""),
            status=state.get("name") or kind, category=category,  # type: ignore[arg-type]
            assignee=(node.get("assignee") or {}).get("name"),
            priority=node.get("priorityLabel"), points=node.get("estimate"),
            due=to_date(node.get("dueDate")), updated=to_date(node.get("updatedAt")),
            url=node.get("url"), project=(node.get("team") or {}).get("key"),
        )  # fmt: skip

    async def search_work_items(
        self, project: str | None = None, query: str | None = None,
        status: Category3 | None = None, limit: int = 25,
    ) -> list[WorkItem]:  # fmt: skip
        teams = [_allowed(project, self.team_keys, "Linear team")] if project else self.team_keys
        filters: dict[str, Any] = {}
        if teams:
            filters["team"] = {"key": {"in": teams}}
        if status:
            filters["state"] = {"type": {"in": _LINEAR_STATES[status]}}
        if query:
            filters["title"] = {"containsIgnoreCase": query[:200]}
        data = await self._gql(
            f"query($f: IssueFilter, $n: Int) {{ issues(filter: $f, first: $n, "
            f"orderBy: updatedAt) {{ nodes {{ {_LINEAR_FIELDS} }} }} }}",
            {"f": filters, "n": limit},
        )
        return [self._item(n) for n in (data.get("issues") or {}).get("nodes", [])]

    async def get_work_item(self, key: str) -> WorkItem:
        data = await self._gql(f"query($id: String!) {{ issue(id: $id) {{ {_LINEAR_FIELDS} }} }}",
                               {"id": safe_id(key, "Linear issue")})  # fmt: skip
        if not data.get("issue"):
            raise ValueError(f"Linear issue {key} not found")
        return self._item(data["issue"])

    async def list_pull_requests(self, repository: str | None = None, state: str = "open",
                                 limit: int = 25) -> list[PullRequest]:  # fmt: skip
        return []  # Linear links PRs from GitHub/GitLab; read them there

    async def health_check(self) -> dict[str, Any]:
        return await self._gql("query { viewer { id } }")


# ------------------------------------------------------------------ GitLab


class GitLabService(ConnectorService):
    source = "gitlab"

    def __init__(self, client_factory: Any, projects: list[str] | None = None) -> None:
        super().__init__(client_factory)
        self.projects = projects or []

    def _item(self, raw: dict[str, Any]) -> WorkItem:
        labels = [str(label).lower() for label in raw.get("labels") or []]
        state = raw.get("state") or "opened"
        category: Category3 = "done" if state == "closed" else (
            "in_progress" if any(w in label for label in labels for w in ("doing", "progress", "review"))
            else "todo"
        )  # fmt: skip
        reference = (raw.get("references") or {}).get("full") or f"#{raw.get('iid')}"
        return WorkItem(
            source=self.source, key=reference, title=raw.get("title", ""), status=state,
            category=category, assignee=((raw.get("assignees") or [{}])[0] or {}).get("name"),
            priority=next((label for label in raw.get("labels") or [] if "priority" in label.lower()), None),
            points=raw.get("weight"), due=to_date(raw.get("due_date")),
            updated=to_date(raw.get("updated_at")), url=raw.get("web_url"),
            project=reference.split("#")[0] or None,
        )  # fmt: skip

    def _project(self, project: str) -> str:
        return quote(_allowed(project, self.projects, "GitLab project"), safe="")

    async def search_work_items(
        self, project: str | None = None, query: str | None = None,
        status: Category3 | None = None, limit: int = 25,
    ) -> list[WorkItem]:  # fmt: skip
        params: dict[str, Any] = {"per_page": limit, "order_by": "updated_at",
                                  "state": "closed" if status == "done" else "opened" if status else "all"}  # fmt: skip
        if query:
            params["search"] = query[:200]
        path = f"/projects/{self._project(project)}/issues" if project else "/issues"
        if not project:
            params["scope"] = "all"
        items = [self._item(i) for i in await self.client.get(path, params)]
        if not project and self.projects:
            allowed = {p.lower() for p in self.projects}
            items = [i for i in items if (i.project or "").lower() in allowed]
        return [i for i in items if not status or i.category == status]

    async def get_work_item(self, key: str) -> WorkItem:
        project, _, iid = key.rpartition("#")
        if not project or not iid.isdigit():
            raise ValueError("GitLab issue keys look like group/project#123")
        return self._item(await self.client.get(f"/projects/{self._project(project)}/issues/{iid}"))

    async def list_pull_requests(self, repository: str | None = None, state: str = "open",
                                 limit: int = 25) -> list[PullRequest]:  # fmt: skip
        if not repository:
            raise ValueError("repository (group/project) is required for GitLab merge requests")
        gl_state = {"open": "opened", "closed": "closed", "merged": "merged"}.get(state, "all")
        rows = await self.client.get(f"/projects/{self._project(repository)}/merge_requests",
                                     {"state": gl_state, "per_page": limit})  # fmt: skip
        return [PullRequest(source=self.source, repository=repository, number=r.get("iid", 0),
                            title=r.get("title", ""), state=r.get("state", ""),
                            author=(r.get("author") or {}).get("name"),
                            updated=to_date(r.get("updated_at")), url=r.get("web_url"))
                for r in rows]  # fmt: skip

    async def health_check(self) -> dict[str, Any]:
        user = await self.client.get("/user")
        return {"user": user.get("username")}


# ------------------------------------------------------------------ Azure DevOps

_ADO_STATES = {"todo": ("New", "To Do", "Proposed", "Approved"),
               "in_progress": ("Active", "Committed", "In Progress", "Doing"),
               "done": ("Done", "Closed", "Resolved", "Removed")}  # fmt: skip
_ADO_FIELDS = ("System.Id,System.Title,System.State,System.AssignedTo,System.TeamProject,"
               "Microsoft.VSTS.Common.Priority,System.ChangedDate,"
               "Microsoft.VSTS.Scheduling.StoryPoints,Microsoft.VSTS.Scheduling.DueDate")  # fmt: skip
API = {"api-version": "7.1"}


class AzureDevOpsService(ConnectorService):
    source = "azure_devops"

    def __init__(self, client_factory: Any, projects: list[str] | None = None) -> None:
        super().__init__(client_factory)
        self.projects = projects or []

    def _item(self, raw: dict[str, Any]) -> WorkItem:
        f = raw.get("fields") or {}
        state = f.get("System.State") or ""
        category = next(
            (c for c, names in _ADO_STATES.items() if state in names), category_of(state)
        )
        assigned = f.get("System.AssignedTo")
        return WorkItem(
            source=self.source, key=str(raw.get("id") or f.get("System.Id")),
            title=f.get("System.Title", ""), status=state, category=category,  # type: ignore[arg-type]
            assignee=assigned.get("displayName") if isinstance(assigned, dict) else assigned,
            priority=str(f["Microsoft.VSTS.Common.Priority"]) if f.get("Microsoft.VSTS.Common.Priority") else None,
            points=f.get("Microsoft.VSTS.Scheduling.StoryPoints"),
            due=to_date(f.get("Microsoft.VSTS.Scheduling.DueDate")),
            updated=to_date(f.get("System.ChangedDate")),
            url=((raw.get("_links") or {}).get("html") or {}).get("href"),
            project=f.get("System.TeamProject"),
        )  # fmt: skip

    def _project(self, project: str | None) -> str:
        if not project:
            if not self.projects:
                raise ValueError("project is required (or set AZURE_DEVOPS_PROJECTS)")
            project = self.projects[0]
        return _allowed(project, self.projects, "Azure DevOps project")

    async def search_work_items(
        self, project: str | None = None, query: str | None = None,
        status: Category3 | None = None, limit: int = 25,
    ) -> list[WorkItem]:  # fmt: skip
        name = self._project(project)
        clauses = ["[System.TeamProject] = @project"]
        if status:
            states = ", ".join(f"'{s}'" for s in _ADO_STATES[status])
            clauses.append(f"[System.State] IN ({states})")
        if query:
            clauses.append(f"[System.Title] CONTAINS '{query[:200].replace(chr(39), chr(39) * 2)}'")
        # WIQL, not SQL: the project name is validated and quotes in the text are doubled.
        wiql = ("SELECT [System.Id] FROM WorkItems WHERE " + " AND ".join(clauses)  # noqa: S608
                + " ORDER BY [System.ChangedDate] DESC")  # fmt: skip
        result = await self.client.post(f"/{quote(name)}/_apis/wit/wiql", {"query": wiql},
                                        {**API, "$top": limit})  # fmt: skip
        ids = [str(w["id"]) for w in result.get("workItems", [])][:limit]
        if not ids:
            return []
        batch = await self.client.get("/_apis/wit/workitems",
                                      {**API, "ids": ",".join(ids), "fields": _ADO_FIELDS})  # fmt: skip
        return [self._item(w) for w in batch.get("value", [])]

    async def get_work_item(self, key: str) -> WorkItem:
        if not key.isdigit():
            raise ValueError("Azure DevOps work item ids are numbers")
        return self._item(await self.client.get(f"/_apis/wit/workitems/{key}",
                                                {**API, "$expand": "links"}))  # fmt: skip

    async def list_pull_requests(self, repository: str | None = None, state: str = "open",
                                 limit: int = 25) -> list[PullRequest]:  # fmt: skip
        name = self._project(None if not repository else repository.split("/")[0])
        status = {"open": "active", "closed": "abandoned", "merged": "completed"}.get(state, "all")
        rows = await self.client.get(f"/{quote(name)}/_apis/git/pullrequests",
                                     {**API, "searchCriteria.status": status, "$top": limit})  # fmt: skip
        return [PullRequest(source=self.source,
                            repository=(r.get("repository") or {}).get("name", name),
                            number=r.get("pullRequestId", 0), title=r.get("title", ""),
                            state=r.get("status", ""),
                            author=(r.get("createdBy") or {}).get("displayName"),
                            updated=to_date(r.get("creationDate")), url=r.get("url"))
                for r in rows.get("value", [])]  # fmt: skip

    async def health_check(self) -> dict[str, Any]:
        body = await self.client.get("/_apis/projects", {**API, "$top": 1})
        return {"projects": body.get("count")}


# ------------------------------------------------------------------ Asana

_ASANA_FIELDS = (
    "name,completed,assignee.name,due_on,modified_at,permalink_url,memberships.section.name"
)


class AsanaService(ConnectorService):
    source = "asana"

    def __init__(self, client_factory: Any, projects: list[str] | None = None) -> None:
        super().__init__(client_factory)
        self.projects = projects or []

    def _item(self, raw: dict[str, Any], project: str | None = None) -> WorkItem:
        section = next((((m.get("section") or {}).get("name")) for m in raw.get("memberships") or []
                        if m.get("section")), None)  # fmt: skip
        status = "Completed" if raw.get("completed") else section or "Open"
        return WorkItem(
            source=self.source, key=str(raw.get("gid", "")), title=raw.get("name", ""),
            status=status, category="done" if raw.get("completed") else category_of(status),
            assignee=(raw.get("assignee") or {}).get("name"), due=to_date(raw.get("due_on")),
            updated=to_date(raw.get("modified_at")), url=raw.get("permalink_url"), project=project,
        )  # fmt: skip

    async def search_work_items(
        self, project: str | None = None, query: str | None = None,
        status: Category3 | None = None, limit: int = 25,
    ) -> list[WorkItem]:  # fmt: skip
        projects = [_allowed(project, self.projects, "Asana project")] if project else self.projects
        if not projects:
            raise ValueError("project (gid) is required (or set ASANA_PROJECTS)")
        items: list[WorkItem] = []
        for gid in projects:
            body = await self.client.get(f"/projects/{gid}/tasks",
                                         {"opt_fields": _ASANA_FIELDS, "limit": min(100, limit * 2)})  # fmt: skip
            items += [self._item(t, gid) for t in body.get("data", [])]
        return [i for i in items if _match(i, query, status)][:limit]

    async def get_work_item(self, key: str) -> WorkItem:
        body = await self.client.get(
            f"/tasks/{safe_id(key, 'Asana task')}", {"opt_fields": _ASANA_FIELDS}
        )
        return self._item(body.get("data") or {})

    async def list_pull_requests(self, repository: str | None = None, state: str = "open",
                                 limit: int = 25) -> list[PullRequest]:  # fmt: skip
        return []

    async def health_check(self) -> dict[str, Any]:
        body = await self.client.get("/users/me")
        return {"user": (body.get("data") or {}).get("name")}


# ------------------------------------------------------------------ ClickUp


class ClickUpService(ConnectorService):
    source = "clickup"

    def __init__(self, client_factory: Any, lists: list[str] | None = None) -> None:
        super().__init__(client_factory)
        self.lists = lists or []

    def _item(self, raw: dict[str, Any], list_id: str | None = None) -> WorkItem:
        status = raw.get("status") or {}
        kind = status.get("type") or ""
        name = status.get("status") or kind
        category: Category3 = "done" if kind in {"closed", "done"} else (
            "in_progress" if kind == "custom" or category_of(name) == "in_progress" else "todo")  # fmt: skip
        return WorkItem(
            source=self.source, key=raw.get("custom_id") or str(raw.get("id", "")),
            title=raw.get("name", ""), status=name, category=category,
            assignee=((raw.get("assignees") or [{}])[0] or {}).get("username"),
            priority=(raw.get("priority") or {}).get("priority"), points=raw.get("points"),
            due=to_date(raw.get("due_date")), updated=to_date(raw.get("date_updated")),
            url=raw.get("url"), project=list_id,
        )  # fmt: skip

    async def search_work_items(
        self, project: str | None = None, query: str | None = None,
        status: Category3 | None = None, limit: int = 25,
    ) -> list[WorkItem]:  # fmt: skip
        lists = [_allowed(project, self.lists, "ClickUp list")] if project else self.lists
        if not lists:
            raise ValueError("project (list id) is required (or set CLICKUP_LISTS)")
        items: list[WorkItem] = []
        for list_id in lists:
            body = await self.client.get(f"/list/{list_id}/task",
                                         {"include_closed": "true", "page": 0})  # fmt: skip
            items += [self._item(t, list_id) for t in body.get("tasks", [])]
        return [i for i in items if _match(i, query, status)][:limit]

    async def get_work_item(self, key: str) -> WorkItem:
        return self._item(await self.client.get(f"/task/{safe_id(key, 'ClickUp task')}"))

    async def list_pull_requests(self, repository: str | None = None, state: str = "open",
                                 limit: int = 25) -> list[PullRequest]:  # fmt: skip
        return []

    async def health_check(self) -> dict[str, Any]:
        body = await self.client.get("/user")
        return {"user": (body.get("user") or {}).get("username")}


# ------------------------------------------------------------------ Bitbucket


class BitbucketService(ConnectorService):
    source = "bitbucket"

    def __init__(self, client_factory: Any, workspace: str, repos: list[str] | None = None) -> None:
        super().__init__(client_factory)
        self.workspace = workspace
        self.repos = repos or []

    def _repo(self, repo: str | None) -> str:
        if not repo:
            if not self.repos:
                raise ValueError("repository is required (or set BITBUCKET_REPOS)")
            repo = self.repos[0]
        repo = repo.split("/")[-1]
        return f"{safe_id(self.workspace, 'workspace')}/{_allowed(repo, self.repos, 'Bitbucket repository')}"

    async def search_work_items(
        self, project: str | None = None, query: str | None = None,
        status: Category3 | None = None, limit: int = 25,
    ) -> list[WorkItem]:  # fmt: skip
        path = self._repo(project)
        terms = []
        if query:
            terms.append(f'title ~ "{query[:200].replace(chr(34), "")}"')
        if status:
            states = {"todo": ("new", "open"), "in_progress": ("open",),
                      "done": ("resolved", "closed", "duplicate", "wontfix")}[status]  # fmt: skip
            terms.append("(" + " OR ".join(f'state = "{s}"' for s in states) + ")")
        params: dict[str, Any] = {"pagelen": min(limit, 50)}
        if terms:
            params["q"] = " AND ".join(terms)
        body = await self.client.get(f"/repositories/{path}/issues", params)
        return [WorkItem(source=self.source, key=f"{path.split('/')[1]}#{i.get('id')}",
                         title=i.get("title", ""), status=i.get("state", ""),
                         category=category_of(i.get("state", "")),
                         assignee=(i.get("assignee") or {}).get("display_name"),
                         priority=i.get("priority"), updated=to_date(i.get("updated_on")),
                         url=((i.get("links") or {}).get("html") or {}).get("href"),
                         project=path.split("/")[1])
                for i in body.get("values", [])]  # fmt: skip

    async def get_work_item(self, key: str) -> WorkItem:
        repo, _, number = key.rpartition("#")
        if not number.isdigit():
            raise ValueError("Bitbucket issue keys look like repo#12")
        i = await self.client.get(f"/repositories/{self._repo(repo)}/issues/{number}")
        return WorkItem(source=self.source, key=key, title=i.get("title", ""),
                        status=i.get("state", ""), category=category_of(i.get("state", "")),
                        assignee=(i.get("assignee") or {}).get("display_name"),
                        updated=to_date(i.get("updated_on")))  # fmt: skip

    async def list_pull_requests(self, repository: str | None = None, state: str = "open",
                                 limit: int = 25) -> list[PullRequest]:  # fmt: skip
        path = self._repo(repository)
        bb_state = {"open": "OPEN", "merged": "MERGED", "closed": "DECLINED"}.get(state)
        params: dict[str, Any] = {"pagelen": min(limit, 50)}
        if bb_state:
            params["state"] = bb_state
        body = await self.client.get(f"/repositories/{path}/pullrequests", params)
        return [PullRequest(source=self.source, repository=path, number=p.get("id", 0),
                            title=p.get("title", ""), state=p.get("state", "").lower(),
                            author=(p.get("author") or {}).get("display_name"),
                            updated=to_date(p.get("updated_on")),
                            url=((p.get("links") or {}).get("html") or {}).get("href"))
                for p in body.get("values", [])]  # fmt: skip

    async def health_check(self) -> dict[str, Any]:
        user = await self.client.get("/user")
        return {"user": user.get("display_name")}


# ------------------------------------------------------------------ demo twin


class DemoTracker:
    """In-memory tracker serving connectors.json data with the same interface."""

    def __init__(self, source: str, data: dict[str, Any]) -> None:
        self.source = source
        self.items = [WorkItem(source=source, **w) for w in data.get("work_items", [])]
        self.pulls = [PullRequest(source=source, **p) for p in data.get("pull_requests", [])]

    async def search_work_items(self, project: str | None = None, query: str | None = None,
                                status: Category3 | None = None, limit: int = 25) -> list[WorkItem]:  # fmt: skip
        rows = [
            i for i in self.items if not project or (i.project or "").lower() == project.lower()
        ]
        return [i for i in rows if _match(i, query, status)][:limit]

    async def get_work_item(self, key: str) -> WorkItem:
        for item in self.items:
            if item.key.lower() == key.lower():
                return item
        raise ValueError(f"{self.source} item {key} not found")

    async def list_pull_requests(self, repository: str | None = None, state: str = "open",
                                 limit: int = 25) -> list[PullRequest]:  # fmt: skip
        rows = [p for p in self.pulls if state == "all" or p.state.lower().startswith(state[:4])]
        return [p for p in rows if not repository or p.repository == repository][:limit]

    async def health_check(self) -> dict[str, str]:
        return {"mode": "demo"}

    async def close(self) -> None:
        return None


def _demo(source: str) -> Any:
    return lambda data, today: DemoTracker(source, data.get("tracker", {}).get(source, {}))


# ------------------------------------------------------------------ specs


def _client(s: Settings, name: str, base_url: str, **kwargs: Any) -> Any:
    return lambda: ConnectorClient(
        name, base_url=base_url, timeout=s.connector_timeout_seconds, **kwargs
    )


TRACKER_SPECS = (
    ConnectorSpec(
        "linear", "Linear", "tracker", required=("linear_api_key",),
        optional=("linear_api_url", "linear_team_keys"),
        setup="Linear → Settings → API → Personal API keys; LINEAR_TEAM_KEYS limits teams (e.g. [\"ENG\"])",
        docs_url="https://developers.linear.app/docs/graphql/working-with-the-graphql-api",
        build=lambda s: LinearService(
            _client(s, "Linear", "{0.scheme}://{0.netloc}".format(urlsplit(s.linear_api_url)),
                    headers={"Authorization": s.linear_api_key.get_secret_value()}),
            s.linear_team_keys, urlsplit(s.linear_api_url).path or "/graphql"),
        demo=_demo("linear"),
    ),
    ConnectorSpec(
        "gitlab", "GitLab", "tracker", required=("gitlab_token",),
        optional=("gitlab_url", "gitlab_projects"),
        setup="Personal/project access token with read_api scope; self-managed: set GITLAB_URL",
        docs_url="https://docs.gitlab.com/api/rest/",
        build=lambda s: GitLabService(_client(s, "GitLab", f"{s.gitlab_url.rstrip('/')}/api/v4",
                                              headers={"PRIVATE-TOKEN": s.gitlab_token.get_secret_value()}),
                                      s.gitlab_projects),
        demo=_demo("gitlab"),
    ),
    ConnectorSpec(
        "azure_devops", "Azure DevOps", "tracker",
        required=("azure_devops_org_url", "azure_devops_pat"), optional=("azure_devops_projects",),
        setup="User settings → Personal access tokens (Work Items: Read, Code: Read)",
        docs_url="https://learn.microsoft.com/rest/api/azure/devops/",
        build=lambda s: AzureDevOpsService(_client(s, "Azure DevOps", s.azure_devops_org_url,
                                                   auth=("", s.azure_devops_pat.get_secret_value())),
                                           s.azure_devops_projects),
        demo=_demo("azure_devops"),
    ),
    ConnectorSpec(
        "asana", "Asana", "tracker", required=("asana_token",), optional=("asana_projects",),
        setup="Asana → My settings → Apps → Developer console → Personal access token",
        docs_url="https://developers.asana.com/reference/rest-api-reference",
        build=lambda s: AsanaService(_client(s, "Asana", "https://app.asana.com/api/1.0",
                                             headers={"Authorization": f"Bearer {s.asana_token.get_secret_value()}"}),
                                     s.asana_projects),
        demo=_demo("asana"),
    ),
    ConnectorSpec(
        "clickup", "ClickUp", "tracker", required=("clickup_token",), optional=("clickup_lists",),
        setup="ClickUp → Settings → Apps → API token; CLICKUP_LISTS = list ids to read",
        docs_url="https://developer.clickup.com/reference",
        build=lambda s: ClickUpService(_client(s, "ClickUp", "https://api.clickup.com/api/v2",
                                               headers={"Authorization": s.clickup_token.get_secret_value()}),
                                       s.clickup_lists),
        demo=_demo("clickup"),
    ),
    ConnectorSpec(
        "bitbucket", "Bitbucket Cloud", "tracker",
        required=("bitbucket_workspace", "bitbucket_username", "bitbucket_api_token"),
        optional=("bitbucket_repos",),
        setup="Atlassian account → API tokens (Bitbucket scopes: repository, pull request read)",
        docs_url="https://developer.atlassian.com/cloud/bitbucket/rest/",
        build=lambda s: BitbucketService(_client(s, "Bitbucket", "https://api.bitbucket.org/2.0",
                                                 auth=(s.bitbucket_username, s.bitbucket_api_token.get_secret_value())),
                                         s.bitbucket_workspace, s.bitbucket_repos),
        demo=_demo("bitbucket"),
    ),
)  # fmt: skip
