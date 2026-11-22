from __future__ import annotations
import asyncio
import json
from typing import Any
from unicodedata import name

import httpx


class ERPNextError(Exception):
    """Structured error from ERPNext API.

    Carries Frappe's rich error fields so callers (especially MCP tools) can
    surface actionable information instead of an opaque HTTP status.
    """
    def __init__(
        self,
        status: int,
        exc_type: str | None,
        message: str,
        server_messages: list | None = None,
        payload: dict | None = None,
    ):
        self.status = status
        self.exc_type = exc_type
        self.message = message
        self.server_messages = server_messages
        self.payload = payload or {}
        super().__init__(message)
        
class ERPNextClient:
    def __init__(self, url: str, api_key: str, api_secret: str):
        self.base_url = url.rstrip("/")
        self.headers = {
            "Authorization": f"token {api_key}:{api_secret}",
           # "Content-Type": "application/json",
            "Accept": "application/json",
        }
        self._client: httpx.AsyncClient | None = None

    # async def _get_client(self) -> httpx.AsyncClient:
    #     if self._client is None or self._client.is_closed:
    #         self._client = httpx.AsyncClient(
    #             base_url=self.base_url,
    #             headers=self.headers,
    #             timeout=30.0,
    #         )
    #     return self._client
    
    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                headers=self.headers,
                timeout=30.0,
                transport=httpx.AsyncHTTPTransport(retries=3),
                limits=httpx.Limits(
                    max_connections=20,
                    max_keepalive_connections=10,
                ),
            )
        return self._client

    async def close(self):
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            
    async def __aenter__(self) -> "ERPNextClient":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.close()

    # async def _request(self, method: str, path: str, **kwargs) -> Any:
    #     client = await self._get_client()
    #     resp = await client.request(method, path, **kwargs)
    #     resp.raise_for_status()
    #     return resp.json()
    
    async def _request(self, method: str, path: str, **kwargs) -> Any:
        client = await self._get_client()
        try:
            resp = await client.request(method, path, **kwargs)
        except httpx.HTTPError as e:
            # Network-level failure (timeout, DNS, connection reset, ...).
            # status=0 signals "no HTTP response received".
            raise ERPNextError(0, "NetworkError", str(e)) from e

        if resp.is_error:
            # Frappe returns very informative error bodies. Preserve them.
            try:
                body = resp.json()
            except Exception:
                body = {}

            server_messages = body.get("_server_messages")
            if isinstance(server_messages, str):
                try:
                    server_messages = json.loads(server_messages)
                except json.JSONDecodeError:
                    pass  # leave as-is; something non-JSON from a proxy

            msg = (
                body.get("exception")
                or body.get("message")
                or resp.text[:500]
                or f"HTTP {resp.status_code}"
            )

            raise ERPNextError(
                resp.status_code,
                body.get("exc_type"),
                msg,
                server_messages=server_messages,
                payload=body,
            )

        return resp.json()
    # --- CRUD ---

    async def get_list(
        self,
        doctype: str,
        fields: list[str] | None = None,
        filters: Any = None,
        or_filters: Any = None,
        order_by: str | None = None,
        limit_start: int = 0,
        limit_page_length: int = 20,
    ) -> list[dict]:
        params: dict[str, Any] = {
            "limit_start": limit_start,
            "limit_page_length": limit_page_length,
        }
        if fields:
            params["fields"] = json.dumps(fields)
        if filters:
            params["filters"] = json.dumps(filters)
        if or_filters:
            params["or_filters"] = json.dumps(or_filters)
        if order_by:
            params["order_by"] = order_by

        result = await self._request("GET", f"/api/resource/{doctype}", params=params)
        return result.get("data", [])

    async def get_doc(self, doctype: str, name: str, fields: list[str] | None = None) -> dict:
        params = {}
        if fields:
            params["fields"] = json.dumps(fields)
        result = await self._request("GET", f"/api/resource/{doctype}/{name}", params=params)
        return result.get("data", {})

    async def create_doc(self, doctype: str, data: dict) -> dict:
        result = await self._request("POST", f"/api/resource/{doctype}", json={"data": json.dumps(data)})
        return result.get("data", {})

    async def update_doc(self, doctype: str, name: str, data: dict) -> dict:
        result = await self._request("PUT", f"/api/resource/{doctype}/{name}", json={"data": json.dumps(data)})
        return result.get("data", {})

    async def delete_doc(self, doctype: str, name: str) -> dict:
        result = await self._request("DELETE", f"/api/resource/{doctype}/{name}")
        return result

    # --- Methods ---

    async def call_method(self, method: str, http_method: str = "GET", **kwargs) -> Any:
        if http_method.upper() == "POST":
            result = await self._request("POST", f"/api/method/{method}", json=kwargs)
        else:
            result = await self._request("GET", f"/api/method/{method}", params=kwargs)
        return result

    # --- Document workflow ---

    # async def submit_doc(self, doctype: str, name: str) -> dict:
    #     doc = await self.get_doc(doctype, name)
    #     doc["docstatus"] = 1
    #     return await self.call_method(
    #         "frappe.client.submit",
    #         http_method="POST",
    #         doc=json.dumps(doc),
    #     )
    async def submit_doc(self, doctype: str, name: str) -> dict:
       return await self.update_doc(doctype, name, {"docstatus": 1})
   
   
    async def cancel_doc(self, doctype: str, name: str) -> dict:
        return await self.call_method(
            "frappe.client.cancel",
            http_method="POST",
            doctype=doctype,
            name=name,
        )

    async def get_count(self, doctype: str, filters: Any = None) -> int:
        params: dict[str, Any] = {"doctype": doctype}
        if filters:
            params["filters"] = json.dumps(filters)
        result = await self.call_method("frappe.client.get_count", **params)
        return result.get("message", 0)

    # async def get_report(self, report_name: str, filters: Any = None) -> Any:
    #     params: dict[str, Any] = {"report_name": report_name}
    #     if filters:
    #         params["filters"] = json.dumps(filters)
    #     return await self.call_method("frappe.desk.query_report.run", **params)
    async def get_report(
        self,
        report_name: str,
        filters: Any = None,
        *,
        ignore_prepared_report: bool = False,
        are_default_filters: bool = False,
        with_filters: bool = False,
    ) -> Any:
        """Run an ERPNext report.

        Args:
            report_name: e.g. "Stock Balance", "General Ledger"
            filters: report filters (dict or list)
            ignore_prepared_report: force fresh execution, don't use the
                prepared-report cache (slower but always current)
            are_default_filters: caller confirms filters equal report defaults
                (allows aggressive caching)
            with_filters: include the report's filter definitions in the response
        """
        params: dict[str, Any] = {"report_name": report_name}
        if filters:
            params["filters"] = json.dumps(filters)
        if ignore_prepared_report:
            params["ignore_prepared_report"] = 1
        if are_default_filters:
            params["are_default_filters"] = 1
        if with_filters:
            params["with_filters"] = 1
        return await self.call_method("frappe.desk.query_report.run", **params)
    
    
    async def search_link(self, doctype: str, txt: str, filters: Any = None, page_length: int = 20) -> list:
        params: dict[str, Any] = {
            "doctype": doctype,
            "txt": txt,
            "page_length": page_length,
        }
        if filters:
            params["filters"] = json.dumps(filters)
        result = await self.call_method("frappe.desk.search.search_link", **params)
        return result.get("message", result.get("results", []))

    # async def get_doctype_meta(self, doctype: str) -> dict:
    #     result = await self.call_method("frappe.client.get_list", doctype="DocField", filters=json.dumps({"parent": doctype}), fields=json.dumps(["fieldname", "fieldtype", "label", "reqd", "options"]), limit_page_length="0")
    #     return result.get("message", [])

    async def get_doctype_meta(self, doctype: str) -> dict:
        result = await self._request("GET", f"/api/resource/DocType/{doctype}")
        d = result.get("data", {})

        # Filter noise fieldtypes so the AI sees the *data fields*, not layout
        # markers. Layout structure is still available via `fields` if you
        # want to keep Section Break etc — see the note below.
        noise = {"Section Break", "Column Break", "Tab Break", "HTML", "Button"}

        fields = [
            {
                k: f.get(k)
                for k in (
                    "fieldname", "fieldtype", "label",
                    "reqd", "options", "default", "description",
                    "depends_on", "read_only", "hidden",
                    "in_list_view", "in_standard_filter",
                )
            }
            for f in d.get("fields", [])
            if f.get("fieldtype") not in noise
        ]

        return {
            "name": d.get("name"),
            "module": d.get("module"),
            "is_submittable": bool(d.get("is_submittable")),
            "issingle": bool(d.get("issingle")),
            "istable": bool(d.get("istable")),
            "title_field": d.get("title_field"),
            "search_fields": d.get("search_fields"),
            "autoname": d.get("autoname"),
            "naming_rule": d.get("naming_rule"),
            "permissions": [
                {
                    "role": p.get("role"),
                    "read": bool(p.get("read")),
                    "write": bool(p.get("write")),
                    "create": bool(p.get("create")),
                    "submit": bool(p.get("submit")),
                    "cancel": bool(p.get("cancel")),
                    "delete": bool(p.get("delete")),
                }
                for p in d.get("permissions", [])
                if p.get("role")
            ],
            "fields": fields,
        }
    # --- Inventory & Trading helpers ---

    # async def get_stock_balance(
    #     self, item_code: str | None = None, warehouse: str | None = None,
    # ) -> list[dict]:
    #     filters: dict[str, Any] = {}
    #     if item_code:
    #         filters["item_code"] = item_code
    #     if warehouse:
    #         filters["warehouse"] = warehouse
    #     result = await self._request(
    #         "GET", "/api/resource/Bin",
    #         params={
    #             "fields": json.dumps(["item_code", "warehouse", "actual_qty", "reserved_qty", "ordered_qty", "projected_qty"]),
    #             "filters": json.dumps(filters),
    #             "limit_page_length": 0,
    #         },
    #     )
    #     return result.get("data", [])
    
    # new-----
    async def get_stock_balance(
        self,
        item_code: str | None = None,
        warehouse: str | None = None,
        *,
        allow_all: bool = False,
        limit: int = 500,
    ) -> list[dict]:
        # Bin is a denormalized summary table with one row per (item, warehouse).
        # An unfiltered `limit_page_length: 0` query scans the whole table —
        # on a mid-size install that's 100k+ rows and 200MB+ of JSON, which
        # can hang ERPNext for minutes. Require at least one filter.
        if not item_code and not warehouse and not allow_all:
            raise ValueError(
                "get_stock_balance requires item_code or warehouse. "
                "Pass allow_all=True to explicitly fetch every Bin row "
                "(expensive — use only for admin/debug tooling)."
            )

        filters: dict[str, Any] = {}
        if item_code:
            filters["item_code"] = item_code
        if warehouse:
            filters["warehouse"] = warehouse

        # Even with allow_all we cap the row count — a paginated iterator
        # would be the right answer here, but a hard cap at least keeps
        # accidental calls bounded.
        page_length = 0 if allow_all and limit is None else limit

        result = await self._request(
            "GET", "/api/resource/Bin",
            params={
                "fields": json.dumps([
                    "item_code", "warehouse", "actual_qty", "reserved_qty",
                    "ordered_qty", "projected_qty",
                ]),
                "filters": json.dumps(filters),
                "limit_page_length": page_length,
            },
        )
        return result.get("data", [])
    
    async def get_item_price(
        self, item_code: str, price_list: str | None = None,
    ) -> list[dict]:
        filters: dict[str, Any] = {"item_code": item_code}
        if price_list:
            filters["price_list"] = price_list
        result = await self._request(
            "GET", "/api/resource/Item Price",
            params={
                "fields": json.dumps(["item_code", "price_list", "price_list_rate", "currency", "uom"]),
                "filters": json.dumps(filters),
                "limit_page_length": 0,
            },
        )
        return result.get("data", [])

    async def make_mapped_doc(self, method: str, source_name: str) -> dict:
        result = await self.call_method(
            method, http_method="POST", source_name=source_name,
        )
        return result.get("message", result)

    async def get_party_balance(self, party_type: str, party: str) -> Any:
        result = await self.call_method(
            "erpnext.accounts.utils.get_balance_on",
            http_method="GET",
            party_type=party_type,
            party=party,
        )
        return result.get("message", 0)

    async def get_stock_ledger(
        self, item_code: str | None = None, warehouse: str | None = None,
        limit: int = 50,
    ) -> list[dict]:
        filters: dict[str, Any] = {}
        if item_code:
            filters["item_code"] = item_code
        if warehouse:
            filters["warehouse"] = warehouse
        result = await self._request(
            "GET", "/api/resource/Stock Ledger Entry",
            params={
                "fields": json.dumps(["item_code", "warehouse", "posting_date", "qty_after_transaction", "actual_qty", "voucher_type", "voucher_no"]),
                "filters": json.dumps(filters),
                "order_by": "posting_date desc, posting_time desc",
                "limit_page_length": limit,
            },
        )
        return result.get("data", [])

    async def find_items(
        self,
        keyword: str,
        item_group: str | None = None,
        brand: str | None = None,
        include_disabled: bool = False,
        limit: int = 20,
    ) -> list[dict]:
        """Fuzzy-search Item by keyword across name / item_name / item_code.

        ERPNext 的 Item code 經常加公司前綴（例如 `CTOS-KV-N40DT`），
        使用者直接用原廠型號（`KV-N40DT`）查 `get_stock_balance` 會回空陣列。
        此方法用 `like %keyword%` 對 name / item_name / item_code 三個欄位做 OR 查詢，
        讓 AI 可以先從關鍵字找到實際 item_code 後再查庫存/價格。
        """
        kw = keyword.strip()
        if not kw:
            return []

        like = f"%{kw}%"
        or_filters: list[list[Any]] = [
            ["Item", "name", "like", like],
            ["Item", "item_name", "like", like],
            ["Item", "item_code", "like", like],
        ]
        filters: list[list[Any]] = []
        if item_group:
            filters.append(["Item", "item_group", "=", item_group])
        if brand:
            filters.append(["Item", "brand", "=", brand])
        if not include_disabled:
            filters.append(["Item", "disabled", "=", 0])

        params: dict[str, Any] = {
            "fields": json.dumps([
                "name", "item_code", "item_name", "item_group",
                "brand", "stock_uom", "disabled", "has_variants",
            ]),
            "or_filters": json.dumps(or_filters),
            "limit_page_length": limit,
        }
        if filters:
            params["filters"] = json.dumps(filters)

        result = await self._request("GET", "/api/resource/Item", params=params)
        return result.get("data", [])

    async def get_item_details(
        self,
        name: str | None = None,
        keyword: str | None = None,
        warehouse: str | None = None,
        price_list: str | None = None,
    ) -> dict:
        """Get full item info (master + stock + price) by exact name or keyword.

        Resolution order:
        1. `name` 直接 get_doc
        2. `keyword` exact name match → fallback to fuzzy `find_items`，取第 1 筆
        """
        if not name and not keyword:
            return {"error": "請提供 name 或 keyword"}

        item: dict[str, Any] | None = None
        candidates: list[dict] = []

        if name:
            try:
                item = await self.get_doc("Item", name)
            except Exception:
                pass

        if item is None and keyword:
            kw = keyword.strip()
            # 先試精確 name
            exact = await self.get_list(
                "Item",
                fields=["name", "item_code", "item_name", "item_group", "brand", "stock_uom", "disabled", "has_variants"],
                filters={"name": kw},
                limit_page_length=1,
            )
            if exact:
                item = await self.get_doc("Item", exact[0]["name"])
            else:
                candidates = await self.find_items(kw, limit=10)
                if candidates:
                    item = await self.get_doc("Item", candidates[0]["name"])

        if item is None:
            return {
                "error": f"找不到品項：{name or keyword}",
                "hint": "可以改用 find_items(keyword=...) 模糊搜尋確認系統內的 item_code",
            }

        item_code = item.get("item_code") or item.get("name")
        stock = await self.get_stock_balance(item_code=item_code, warehouse=warehouse)
        try:
            prices = await self.get_item_price(item_code, price_list=price_list)
        except Exception:
            prices = []

        return {
            "item": {
                "name": item.get("name"),
                "item_code": item.get("item_code"),
                "item_name": item.get("item_name"),
                "item_group": item.get("item_group"),
                "brand": item.get("brand"),
                "stock_uom": item.get("stock_uom"),
                "disabled": bool(item.get("disabled")),
                "has_variants": bool(item.get("has_variants")),
                "description": item.get("description"),
            },
            "stock": stock,
            "prices": prices,
            "other_candidates": [
                {"name": c["name"], "item_name": c.get("item_name")}
                for c in candidates[1:]
            ] if len(candidates) > 1 else [],
        }
    # --- Info & workflow helpers ---

    async def get_doc_info(self, doctype: str, name: str) -> dict:
        """Get summary info about a document without fetching full content."""
        doc = await self.get_doc(doctype, name)

        def _parse_list(v):
            if isinstance(v, str):
                try:
                    return json.loads(v)
                except json.JSONDecodeError:
                    return []
            return v or []

        assign = _parse_list(doc.get("_assign"))
        comments = _parse_list(doc.get("_comments"))
        tags = _parse_list(doc.get("_user_tags"))
        liked_by = _parse_list(doc.get("_liked_by"))

        try:
            attachments_count = await self.get_count(
                "File",
                filters={
                    "attached_to_doctype": doctype,
                    "attached_to_name": name,
                },
            )
        except Exception:
            attachments_count = None

        docstatus = doc.get("docstatus")
        return {
            "doctype": doctype,
            "name": name,
            "docstatus": docstatus,
            "docstatus_label": {
                0: "Draft", 1: "Submitted", 2: "Cancelled",
            }.get(docstatus),
            "status": doc.get("status"),
            "workflow_state": doc.get("workflow_state"),
            "owner": doc.get("owner"),
            "modified_by": doc.get("modified_by"),
            "creation": doc.get("creation"),
            "modified": doc.get("modified"),
            "assigned_to": assign,
            "tags": tags,
            "liked_by_count": len(liked_by),
            "comment_count": len(comments),
            "attachments_count": attachments_count,
        }

    async def get_workflow_actions(
        self, doctype: str, name: str,
    ) -> list[dict]:
        """List workflow transitions available for a document.

        Returns [{action, next_state, allowed, ...}]. Empty list means no
        workflow is configured on this DocType or the doc is in a terminal state.
        """
        doc = await self.get_doc(doctype, name)
        try:
            result = await self.call_method(
                "frappe.model.workflow.get_transitions",
                http_method="POST",
                doc=json.dumps(doc),
            )
        except ERPNextError as e:
            # No workflow → error or empty message; treat as empty
            if e.status == 417 or "No workflow" in (e.message or ""):
                return []
            raise
        return result.get("message", []) or []

    async def apply_workflow(
        self, doctype: str, name: str, action: str,
    ) -> dict:
        """Apply a workflow action (e.g. "Approve", "Reject") to a doc.

        This routes through Frappe's workflow engine, running all workflow
        conditions and side effects. Use `get_workflow_actions()` first to
        see which actions are valid for the doc's current state.
        """
        doc = await self.get_doc(doctype, name)
        result = await self.call_method(
            "frappe.model.workflow.apply_workflow",
            http_method="POST",
            doc=json.dumps(doc),
            action=action,
        )
        return result.get("message", result)

    async def get_linked_docs(self, doctype: str, name: str) -> dict:
        """Check which submitted documents link to this doc.

        Answers "what will break if I delete this?" — if `can_delete` is
        False, ERPNext will refuse the delete until those links are resolved.
        """
        try:
            result = await self.call_method(
                "frappe.desk.form.linked_with.get_submitted_linked_docs",
                http_method="POST",
                doctype=doctype,
                name=name,
            )
            data = result.get("message", result)
        except ERPNextError:
            data = {}

        groups = (data or {}).get("docs", []) if isinstance(data, dict) else []
        total = 0
        for g in groups:
            docs = g.get("docs") or []
            total += len(docs)

        return {
            "doctype": doctype,
            "name": name,
            "can_delete": total == 0,
            "total_links": total,
            "linked_docs": groups,
        }

    async def get_timeline(
        self, doctype: str, name: str, limit: int = 50,
    ) -> dict:
        """Get comments, version history, and emails for a document."""
        comments = await self.get_list(
            "Comment",
            fields=[
                "name", "comment_type", "comment_email", "comment_by",
                "subject", "content", "creation",
            ],
            filters={
                "reference_doctype": doctype,
                "reference_name": name,
            },
            order_by="creation desc",
            limit_page_length=limit,
        )

        versions = await self.get_list(
            "Version",
            fields=["name", "owner", "creation", "data"],
            filters={"ref_doctype": doctype, "docname": name},
            order_by="creation desc",
            limit_page_length=limit,
        )
        for v in versions:
            if isinstance(v.get("data"), str):
                try:
                    v["data"] = json.loads(v["data"])
                except json.JSONDecodeError:
                    pass

        communications = await self.get_list(
            "Communication",
            fields=[
                "name", "communication_type", "subject",
                "sender", "sender_full_name", "creation",
            ],
            filters={
                "reference_doctype": doctype,
                "reference_name": name,
            },
            order_by="creation desc",
            limit_page_length=limit,
        )

        return {
            "doctype": doctype,
            "name": name,
            "comments": comments,
            "versions": versions,
            "communications": communications,
        }

    async def add_comment(
        self, doctype: str, name: str, text: str,
    ) -> dict:
        """Add a comment to a document."""
        return await self.create_doc(
            "Comment",
            {
                "comment_type": "Comment",
                "reference_doctype": doctype,
                "reference_name": name,
                "content": text,
            },
        )

    async def get_permissions(
        self, doctype: str, doc_name: str | None = None,
    ) -> dict:
        """Check the current API user's effective permissions on a DocType.

        Runs 7 `has_permission` checks in parallel — one round-trip total.
        If `doc_name` is provided, permissions are evaluated against that
        specific document (respects owner / workflow-state rules).
        """
        ptypes = [
            "read", "write", "create", "submit",
            "cancel", "delete", "amend",
        ]

        async def check(ptype: str) -> bool | None:
            try:
                params: dict[str, Any] = {
                    "doctype": doctype,
                    "perm_type": ptype,
                }
                if doc_name:
                    params["docname"] = doc_name
                r = await self.call_method(
                    "frappe.client.has_permission",
                    http_method="POST",
                    **params,
                )
                return bool(r.get("message", False))
            except Exception:
                return None

        results = await asyncio.gather(*[check(p) for p in ptypes])
        return {
            "doctype": doctype,
            "doc_name": doc_name,
            "permissions": dict(zip(ptypes, results)),
        }

    async def get_defaults(self) -> dict:
        """Get system-wide defaults: company, currency, warehouse, price lists, fiscal year."""
        result: dict[str, Any] = {}

        try:
            gd = await self.get_doc("Global Defaults", "Global Defaults")
            result.update({
                "default_company": gd.get("default_company"),
                "country": gd.get("country"),
                "currency": gd.get("currency"),
                "hide_currency_symbol": gd.get("hide_currency_symbol"),
                "disable_rounded_total": gd.get("disable_rounded_total"),
            })
        except Exception:
            pass

        try:
            ss = await self.get_doc("Stock Settings", "Stock Settings")
            result["default_warehouse"] = ss.get("default_warehouse")
            result["stock_uom"] = ss.get("stock_uom")
        except Exception:
            pass

        try:
            sell = await self.get_doc("Selling Settings", "Selling Settings")
            result["selling_price_list"] = sell.get("selling_price_list")
            result["customer_naming_by"] = sell.get("cust_master_name")
        except Exception:
            pass

        try:
            buy = await self.get_doc("Buying Settings", "Buying Settings")
            result["buying_price_list"] = buy.get("buying_price_list")
            result["supplier_naming_by"] = buy.get("supp_master_name")
        except Exception:
            pass

        try:
            fy = await self.get_list(
                "Fiscal Year",
                fields=["name", "year_start_date", "year_end_date"],
                filters={"disabled": 0},
                order_by="year_start_date desc",
                limit_page_length=1,
            )
            result["fiscal_year"] = fy[0] if fy else None
        except Exception:
            pass

        return result

    async def get_value(
        self,
        doctype: str,
        name: str,
        field: str | list[str],
    ) -> dict:
        """Fast single-or-multi-field lookup (much cheaper than get_doc)."""
        fieldname = json.dumps(field) if isinstance(field, list) else field
        result = await self.call_method(
            "frappe.client.get_value",
            http_method="POST",
            doctype=doctype,
            filters=json.dumps({"name": name}),
            fieldname=fieldname,
            as_dict=1,
        )
        return result.get("message", {})

    async def set_value(
        self,
        doctype: str,
        name: str,
        field: str | dict[str, Any],
        value: Any = None,
    ) -> dict:
        """Fast single-field (or multi-field if `field` is a dict) update."""
        if isinstance(field, dict):
            fieldname = json.dumps(field)
            val = None
        else:
            fieldname = field
            val = value

        params: dict[str, Any] = {
            "doctype": doctype,
            "name": name,
            "fieldname": fieldname,
        }
        if val is not None:
            params["value"] = val if isinstance(val, str) else json.dumps(val)

        result = await self.call_method(
            "frappe.client.set_value",
            http_method="POST",
            **params,
        )
        return result.get("message", result)

    async def rename_document(
        self,
        doctype: str,
        old_name: str,
        new_name: str,
        merge: bool = False,
    ) -> dict:
        """Rename a document. `merge=True` merges into an existing new_name."""
        result = await self.call_method(
            "frappe.client.rename_doc",
            http_method="POST",
            doctype=doctype,
            old_name=old_name,
            new_name=new_name,
            merge=1 if merge else 0,
        )
        return result.get("message", result)

    async def bulk_create(
        self,
        doctype: str,
        items: list[dict],
        concurrency: int = 1,
    ) -> dict:
        """Create many docs of the same doctype. Returns per-row ok/error.

        `concurrency=1` is sequential (safe); >1 runs creates in parallel,
        capped at N simultaneous requests.
        """
        if not items:
            return {
                "total": 0, "succeeded": 0, "failed": 0, "results": [],
            }

        async def _one(i: int, item: dict) -> dict:
            try:
                doc = await self.create_doc(doctype, item)
                return {"index": i, "ok": True, "name": doc.get("name")}
            except ERPNextError as e:
                return {
                    "index": i, "ok": False,
                    "exc_type": e.exc_type, "error": e.message,
                }
            except Exception as e:
                return {
                    "index": i, "ok": False,
                    "exc_type": type(e).__name__, "error": str(e),
                }

        if concurrency <= 1:
            results = []
            for i, item in enumerate(items):
                results.append(await _one(i, item))
        else:
            sem = asyncio.Semaphore(concurrency)

            async def _bounded(i: int, item: dict) -> dict:
                async with sem:
                    return await _one(i, item)

            results = await asyncio.gather(
                *[_bounded(i, item) for i, item in enumerate(items)]
            )

        ok = sum(1 for r in results if r.get("ok"))
        return {
            "total": len(items),
            "succeeded": ok,
            "failed": len(items) - ok,
            "results": list(results),
        }
    # --- File operations ---

    # async def upload_file(
    #     self,
    #     file_content: bytes,
    #     filename: str,
    #     attached_to_doctype: str | None = None,
    #     attached_to_name: str | None = None,
    #     is_private: bool = True,
    # ) -> dict:
    #     """上傳檔案到 ERPNext。

    #     Args:
    #         file_content: 檔案內容（bytes）
    #         filename: 檔案名稱
    #         attached_to_doctype: 附加到的 DocType（如 "Project"）
    #         attached_to_name: 附加到的文件名稱（如 "PROJ-0001"）
    #         is_private: 是否為私有檔案（預設 True）

    #     Returns:
    #         File 文件資料，包含 file_url 等
    #     """
    #     # 使用獨立的 httpx client 避免 header 衝突
    #     async with httpx.AsyncClient(base_url=self.base_url, timeout=60.0) as client:
           
    #         # 準備 multipart form data
    #         files = {
    #             "file": (filename, file_content),
    #         }
    #         data: dict[str, str] = {
    #             "is_private": "1" if is_private else "0",
    #         }
    #         if attached_to_doctype:
    #             data["doctype"] = attached_to_doctype
    #         if attached_to_name:
    #             data["docname"] = attached_to_name

    #         resp = await client.post(
    #             "/api/method/upload_file",
    #             files=files,
    #             data=data,
    #             headers={
    #                 "Authorization": self.headers["Authorization"],
    #                 "Expect": "",  # 禁用 100-continue，避免 417 錯誤
    #             },
    #         )
    #         resp.raise_for_status()
    #         result = resp.json()
    #         return result.get("message", result)
    
    async def upload_file(
        self,
        file_content: bytes,
        filename: str,
        attached_to_doctype: str | None = None,
        attached_to_name: str | None = None,
        is_private: bool = True,
    ) -> dict:
        """上傳檔案到 ERPNext。

        Args:
            file_content: 檔案內容（bytes）
            filename: 檔案名稱
            attached_to_doctype: 附加到的 DocType（如 "Project"）
            attached_to_name: 附加到的文件名稱（如 "PROJ-0001"）
            is_private: 是否為私有檔案（預設 True）

        Returns:
            File 文件資料，包含 file_url 等
        """
        client = await self._get_client()

        files = {"file": (filename, file_content)}
        data: dict[str, str] = {
            "is_private": "1" if is_private else "0",
        }
        if attached_to_doctype:
            data["doctype"] = attached_to_doctype
        if attached_to_name:
            data["docname"] = attached_to_name

        # `files=` makes httpx set multipart/form-data automatically;
        # we only add Expect: "" to disable 100-continue (some proxies 417 on it).
        resp = await client.post(
            "/api/method/upload_file",
            files=files,
            data=data,
            headers={"Expect": ""},
        )
        resp.raise_for_status()
        result = resp.json()
        return result.get("message", result)

    # async def upload_file_from_url(
    #     self,
    #     file_url: str,
    #     filename: str | None = None,
    #     attached_to_doctype: str | None = None,
    #     attached_to_name: str | None = None,
    #     is_private: bool = True,
    # ) -> dict:
    #     """從 URL 上傳檔案到 ERPNext。

    #     Args:
    #         file_url: 檔案來源 URL
    #         filename: 檔案名稱（可選，會從 URL 推斷）
    #         attached_to_doctype: 附加到的 DocType
    #         attached_to_name: 附加到的文件名稱
    #         is_private: 是否為私有檔案

    #     Returns:
    #         File 文件資料
    #     """
    #     # 使用獨立的 httpx client
    #     async with httpx.AsyncClient(base_url=self.base_url, timeout=60.0) as client:
    #         data: dict[str, str] = {
    #             "file_url": file_url,
    #             "is_private": "1" if is_private else "0",
    #         }
    #         if filename:
    #             data["filename"] = filename
    #         if attached_to_doctype:
    #             data["doctype"] = attached_to_doctype
    #         if attached_to_name:
    #             data["docname"] = attached_to_name

    #         resp = await client.post(
    #             "/api/method/upload_file",
    #             data=data,
    #             headers={"Authorization": self.headers["Authorization"]},
    #         )
    #         resp.raise_for_status()
    #         result = resp.json()
    #         return result.get("message", result)
    
    async def upload_file_from_url(
        self,
        file_url: str,
        filename: str | None = None,
        attached_to_doctype: str | None = None,
        attached_to_name: str | None = None,
        is_private: bool = True,
    ) -> dict:
        """從 URL 上傳檔案到 ERPNext。

        Args:
            file_url: 檔案來源 URL
            filename: 檔案名稱（可選，會從 URL 推斷）
            attached_to_doctype: 附加到的 DocType
            attached_to_name: 附加到的文件名稱
            is_private: 是否為私有檔案

        Returns:
            File 文件資料
        """
        client = await self._get_client()

        data: dict[str, str] = {
            "file_url": file_url,
            "is_private": "1" if is_private else "0",
        }
        if filename:
            data["filename"] = filename
        if attached_to_doctype:
            data["doctype"] = attached_to_doctype
        if attached_to_name:
            data["docname"] = attached_to_name

        resp = await client.post(
            "/api/method/upload_file",
            data=data,
        )
        resp.raise_for_status()
        result = resp.json()
        return result.get("message", result)
    async def list_files(
        self,
        attached_to_doctype: str | None = None,
        attached_to_name: str | None = None,
        is_private: bool | None = None,
        limit: int = 20,
    ) -> list[dict]:
        """列出檔案。

        Args:
            attached_to_doctype: 過濾附加到的 DocType
            attached_to_name: 過濾附加到的文件名稱
            is_private: 過濾私有/公開檔案
            limit: 返回數量上限

        Returns:
            File 文件列表
        """
        filters: dict[str, Any] = {}
        if attached_to_doctype:
            filters["attached_to_doctype"] = attached_to_doctype
        if attached_to_name:
            filters["attached_to_name"] = attached_to_name
        if is_private is not None:
            filters["is_private"] = 1 if is_private else 0

        result = await self._request(
            "GET", "/api/resource/File",
            params={
                "fields": json.dumps([
                    "name", "file_name", "file_url", "file_size",
                    "attached_to_doctype", "attached_to_name",
                    "is_private", "creation", "modified",
                ]),
                "filters": json.dumps(filters) if filters else None,
                "order_by": "creation desc",
                "limit_page_length": limit,
            },
        )
        return result.get("data", [])

    async def get_file_url(self, file_name: str) -> str:
        """取得檔案的完整下載 URL。

        Args:
            file_name: File 文件的 name（如 "abc123.pdf"）

        Returns:
            完整的檔案 URL
        """
        doc = await self.get_doc("File", file_name)
        file_url = doc.get("file_url", "")
        if file_url and not file_url.startswith("http"):
            return f"{self.base_url}{file_url}"
        return file_url

    # async def download_file(self, file_name: str) -> tuple[bytes, str]:
    #     """下載檔案內容。

    #     Args:
    #         file_name: File 文件的 name

    #     Returns:
    #         (檔案內容 bytes, 檔案名稱)
    #     """
    #     doc = await self.get_doc("File", file_name)
    #     file_url = doc.get("file_url", "")
    #     original_filename = doc.get("file_name", file_name)

    #     if not file_url:
    #         raise ValueError(f"File {file_name} has no file_url")

    #     client = await self._get_client()
    #     # 下載時不需要 Content-Type: application/json
    #     resp = await client.get(file_url)
    #     resp.raise_for_status()
    #     return resp.content, original_filename
    async def download_file(
        self,
        file_name: str,
        max_bytes: int = 20 * 1024 * 1024,
    ) -> tuple[bytes, str]:
        """下載檔案內容（串流 + 大小上限）。

        Args:
            file_name: File 文件的 name
            max_bytes: 最大下載大小（預設 20 MB）；超過則拋 ValueError

        Returns:
            (檔案內容 bytes, 檔案名稱)

        Raises:
            ValueError: file_url 缺失，或檔案超過 max_bytes
        """
        doc = await self.get_doc("File", file_name)
        file_url = doc.get("file_url", "")
        original_filename = doc.get("file_name", file_name)

        if not file_url:
            raise ValueError(f"File {file_name} has no file_url")

        client = await self._get_client()
        buf = bytearray()
        async with client.stream("GET", file_url) as resp:
            resp.raise_for_status()
            async for chunk in resp.aiter_bytes():
                buf.extend(chunk)
                if len(buf) > max_bytes:
                    raise ValueError(
                        f"File {file_name} exceeds {max_bytes} bytes "
                        f"(downloaded {len(buf)} bytes before abort). "
                        f"Use get_file_url() to fetch it via browser instead."
                    )

        return bytes(buf), original_filename
    
    async def find_docs(
        self,
        doctype: str,
        keyword: str,
        *,
        search_fields: list[str] | None = None,
        filters: Any = None,
        fields: list[str] | None = None,
        limit: int = 20,
    ) -> list[dict]:
        """Fuzzy-search documents of any DocType across one or more fields.

        Runs a single OR query with `like %keyword%` across `search_fields`.
        Useful to resolve human-typed text ("K&N filter", "KV-N40DT",
        "正達") into actual document names before calling
        get_doc / get_stock_balance / set_value.

        Args:
            doctype: DocType to search in (e.g. "Item", "Customer", "Supplier")
            keyword: text to search for (LIKE %keyword%, case-insensitive)
            search_fields: fields to match against. Defaults to ["name"].
                Common: Item → ["item_name", "item_code"],
                Customer → ["customer_name"], Supplier → ["supplier_name"]
            filters: additional AND filters, Frappe format (list of
                [doctype, field, op, value] triplets, or a dict)
            fields: fields to return. Defaults to ["name"] + search_fields
            limit: max rows to return

        Returns:
            list of dicts, one per matched doc
        """
        kw = (keyword or "").strip()
        if not kw:
            return []

        search_fields = list(search_fields or ["name"])

        # Escape SQL LIKE wildcards so a keyword like "50% off" doesn't
        # match everything.
        escaped = kw.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        like = f"%{escaped}%"

        or_filters = [[doctype, f, "like", like] for f in search_fields]

        # Default output fields: name + search fields (deduped, order preserved)
        if fields is None:
            output_fields: list[str] = ["name"]
            for f in search_fields:
                if f not in output_fields:
                    output_fields.append(f)
        else:
            output_fields = fields

        result = await self._request(
            "GET",
            f"/api/resource/{doctype}",
            params={
                "fields": json.dumps(output_fields),
                "or_filters": json.dumps(or_filters),
                "filters": json.dumps(filters) if filters else None,
                "limit_page_length": limit,
            },
        )
        return result.get("data", [])
 
 
 