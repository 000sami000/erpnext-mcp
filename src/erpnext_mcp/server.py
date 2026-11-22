from __future__ import annotations
import functools
import json
import os
from typing import Any

from dotenv import load_dotenv
from fastmcp import FastMCP

from .client import ERPNextClient, ERPNextError

load_dotenv()
def friendly_errors(fn):
    """Convert exceptions into structured dicts the LLM can reason about.

    FastMCP turns raised exceptions into opaque `ToolError: <str(exc)>` messages.
    Returning a dict instead lets the agent see error type, status, and
    Frappe's server_messages — enabling self-correction.
    """
    @functools.wraps(fn)
    async def wrapper(*args, **kwargs):
        try:
            return await fn(*args, **kwargs)
        except ERPNextError as e:
            result: dict[str, Any] = {
                "error": True,
                "status": e.status,
                "exc_type": e.exc_type,
                "message": e.message,
            }
            if e.server_messages:
                result["details"] = e.server_messages
            return result
        except Exception as e:
            return {
                "error": True,
                "status": 0,
                "exc_type": type(e).__name__,
                "message": str(e),
            }
    return wrapper
mcp = FastMCP(
    "ERPNext",
    instructions="MCP Server for ERPNext REST API - CRUD, reports, workflow operations,Before deleting any document, call get_linked_docs() to check dependencies.",
)

_client: ERPNextClient | None = None


def get_client() -> ERPNextClient:
    global _client
    if _client is None:
        url = os.environ.get("ERPNEXT_URL", "http://ct.erp")
        api_key = os.environ["ERPNEXT_API_KEY"]
        api_secret = os.environ["ERPNEXT_API_SECRET"]
        _client = ERPNextClient(url, api_key, api_secret)
    return _client


# ── CRUD ──────────────────────────────────────────────


@mcp.tool()
@friendly_errors
async def list_documents(
    doctype: str,
    fields: list[str] | None = None,
    filters: str | None = None,
    or_filters: str | None = None,
    order_by: str | None = None,
    limit_start: int = 0,
    limit_page_length: int = 20,
) -> list[dict]:
    """List documents of a given DocType with optional filtering, sorting and pagination.

    Args:
        doctype: ERPNext DocType name (e.g. "Sales Order", "Customer")
        fields: List of field names to return. Defaults to ["name"].
        filters: JSON string of filters, e.g. '{"status": "Open"}' or '[["status","=","Open"]]'
        or_filters: JSON string of OR filters
        order_by: Sort expression, e.g. "creation desc"
        limit_start: Pagination offset
        limit_page_length: Number of records to return (max 100)
    """
    f = json.loads(filters) if filters else None
    of = json.loads(or_filters) if or_filters else None
    return await get_client().get_list(
        doctype, fields=fields, filters=f, or_filters=of,
        order_by=order_by, limit_start=limit_start, limit_page_length=limit_page_length,
    )


@mcp.tool()
@friendly_errors
async def get_document(doctype: str, name: str, fields: list[str] | None = None) -> dict:
    """Get a single document by DocType and name.

    Args:
        doctype: ERPNext DocType name
        name: Document name/ID
        fields: Optional list of fields to return
    """
    return await get_client().get_doc(doctype, name, fields=fields)


@mcp.tool()
@friendly_errors
async def create_document(doctype: str, data: str) -> dict:
    """Create a new document.

    Args:
        doctype: ERPNext DocType name
        data: JSON string of field values, e.g. '{"customer_name": "Test", "customer_type": "Individual"}'
    """
    return await get_client().create_doc(doctype, json.loads(data))


@mcp.tool()
@friendly_errors
async def update_document(doctype: str, name: str, data: str) -> dict:
    """Update an existing document.

    Args:
        doctype: ERPNext DocType name
        name: Document name/ID
        data: JSON string of fields to update
    """
    return await get_client().update_doc(doctype, name, json.loads(data))


@mcp.tool()
@friendly_errors
async def delete_document(doctype: str, name: str) -> dict:
    """Delete a document.

    Args:
        doctype: ERPNext DocType name
        name: Document name/ID
    """
    return await get_client().delete_doc(doctype, name)


# ── Reports ───────────────────────────────────────────


# @mcp.tool()
# @friendly_errors
# async def run_report(report_name: str, filters: str | None = None) -> Any:
#     """Execute an ERPNext report.

#     Args:
#         report_name: Name of the report
#         filters: Optional JSON string of report filters
#     """
#     f = json.loads(filters) if filters else None
#     return await get_client().get_report(report_name, filters=f)

@mcp.tool()
@friendly_errors
async def run_report(
    report_name: str,
    filters: str | None = None,
    ignore_prepared_report: bool = False,
) -> Any:
    """Execute an ERPNext report.

    ⚠️ For long-running reports (Stock Balance, General Ledger, Profit & Loss),
    ERPNext may return a **cached** result that can be hours or days old.
    Set `ignore_prepared_report=True` to force a fresh execution when
    up-to-date numbers matter.

    Args:
        report_name: Name of the report (e.g. "Stock Balance")
        filters: Optional JSON string of report filters
        ignore_prepared_report: If True, bypass cached prepared reports
            and re-execute from scratch (slower but current)
    """
    f = json.loads(filters) if filters else None
    return await get_client().get_report(
        report_name,
        filters=f,
        ignore_prepared_report=ignore_prepared_report,
    )

@mcp.tool()
@friendly_errors
async def get_count(doctype: str, filters: str | None = None) -> int:
    """Get document count for a DocType with optional filters.

    Args:
        doctype: ERPNext DocType name
        filters: Optional JSON string of filters
    """
    f = json.loads(filters) if filters else None
    return await get_client().get_count(doctype, filters=f)


@mcp.tool()
@friendly_errors
async def get_list_with_summary(
    doctype: str,
    fields: list[str] | None = None,
    filters: str | None = None,
    order_by: str | None = None,
    limit_page_length: int = 20,
) -> dict:
    """Get a list of documents along with total count.

    Args:
        doctype: ERPNext DocType name
        fields: Fields to return
        filters: Optional JSON string of filters
        order_by: Sort expression
        limit_page_length: Number of records
    """
    f = json.loads(filters) if filters else None
    client = get_client()
    docs = await client.get_list(doctype, fields=fields, filters=f, order_by=order_by, limit_page_length=limit_page_length)
    count = await client.get_count(doctype, filters=f)
    return {"data": docs, "total_count": count}


# ── Workflow ──────────────────────────────────────────


@mcp.tool()
@friendly_errors
async def submit_document(doctype: str, name: str) -> dict:
    """Submit a submittable document (e.g. Sales Invoice).

    Args:
        doctype: ERPNext DocType name
        name: Document name/ID
    """
    return await get_client().submit_doc(doctype, name)


@mcp.tool()
@friendly_errors
async def cancel_document(doctype: str, name: str) -> dict:
    """Cancel a submitted document.

    Args:
        doctype: ERPNext DocType name
        name: Document name/ID
    """
    return await get_client().cancel_doc(doctype, name)


@mcp.tool()
@friendly_errors
async def run_method(method: str, http_method: str = "POST", args: str | None = None) -> Any:
    """Call a server-side method (whitelisted API).

    Args:
        method: Dotted method path, e.g. "frappe.client.get_list" or "erpnext.selling.doctype.sales_order.sales_order.make_delivery_note"
        http_method: GET or POST (default POST)
        args: Optional JSON string of keyword arguments
    """
    kwargs = json.loads(args) if args else {}
    return await get_client().call_method(method, http_method=http_method, **kwargs)


# ── Helpers ───────────────────────────────────────────


@mcp.tool()
@friendly_errors
async def list_doctypes(module: str | None = None, is_submittable: bool | None = None, limit: int = 100) -> list[str]:
    """List all available DocType names.

    Args:
        module: Optional module filter (e.g. "Selling", "Stock", "Accounts")
        is_submittable: Optional filter for submittable doctypes only
        limit: Max results (default 100)
    """
    filters: dict[str, Any] = {}
    if module:
        filters["module"] = module
    if is_submittable is not None:
        filters["is_submittable"] = int(is_submittable)
    docs = await get_client().get_list(
        "DocType", fields=["name"], filters=filters or None,
        order_by="name asc", limit_page_length=limit,
    )
    return [d["name"] for d in docs]


@mcp.tool()
@friendly_errors
async def search_link(doctype: str, txt: str, filters: str | None = None, page_length: int = 20) -> list:
    """Search for link field values (autocomplete).

    Args:
        doctype: DocType to search in
        txt: Search text
        filters: Optional JSON string of filters
        page_length: Max results
    """
    f = json.loads(filters) if filters else None
    return await get_client().search_link(doctype, txt, filters=f, page_length=page_length)


# @mcp.tool()
# async def get_doctype_meta(doctype: str) -> list:
#     """Get field definitions for a DocType.

#     Args:
#         doctype: ERPNext DocType name
#     """
#     return await get_client().get_doctype_meta(doctype)

@mcp.tool()
@friendly_errors
async def get_doctype_meta(doctype: str) -> dict:
    """Get complete metadata for a DocType: fields, permissions, and structural flags.

    Use this before attempting create/update/submit operations to learn:
    - which fields exist and whether they're required (`reqd`) or conditional (`depends_on`)
    - whether the doc is submittable (`is_submittable`) — determines if submit/cancel apply
    - whether it's a singleton (`issingle`) or child table (`istable`) — affects how to fetch it
    - which roles can read / write / submit / cancel (`permissions`)

    Args:
        doctype: ERPNext DocType name (e.g. "Sales Order", "Item", "Customer")
    """
    return await get_client().get_doctype_meta(doctype)
# ── Inventory & Trading ──────────────────────────────


# @mcp.tool()
# async def get_stock_balance(item_code: str | None = None, warehouse: str | None = None) -> list[dict]:
#     """Get real-time stock balance from Bin (exact match on item_code).

#     ⚠️ 此工具對 item_code 做**精確比對**。回傳空陣列代表「找不到對應 Bin 紀錄」，
#     可能是真的零庫存，也可能是 item_code 拼錯或缺少公司前綴（例如系統實際是
#     `CTOS-KV-N40DT` 而你只查了 `KV-N40DT`）。**若回傳空陣列，請改用
#     `find_items(keyword=...)` 或 `get_item_details(keyword=...)` 模糊確認**。

#     Args:
#         item_code: Optional exact item code to filter
#         warehouse: Optional warehouse to filter
#     """
#     return await get_client().get_stock_balance(item_code=item_code, warehouse=warehouse)

# new-------
@mcp.tool()
@friendly_errors
async def get_stock_balance(
    item_code: str | None = None,
    warehouse: str | None = None,
    limit: int = 500,
) -> list[dict]:
    """Get real-time stock balance from Bin.

    **At least one of `item_code` or `warehouse` is required.** Calling with
    no filters is intentionally blocked because it would scan every row in
    the Bin table (potentially 100k+ rows, multi-minute server load).
    To browse all stock, use `find_items()` first to resolve item codes,
    then query per item.

    ⚠️ `item_code` is matched **exactly**. Empty result means "no Bin row
    matched" — either genuinely zero stock, or the code is wrong/missing a
    company prefix (e.g. the system stores `CTOS-KV-N40DT` but you queried
    `KV-N40DT`). If you get an empty array, call `find_items(keyword=...)`
    or `get_item_details(keyword=...)` to resolve the real item_code first.

    Args:
        item_code: Exact item code to filter. Required unless `warehouse` is set.
        warehouse: Warehouse name to filter. Required unless `item_code` is set.
        limit: Max rows to return (default 500). Increase only if you know
               the item has many warehouses/locations.
    """
    return await get_client().get_stock_balance(
        item_code=item_code, warehouse=warehouse, limit=limit,
    )
    
@mcp.tool()
@friendly_errors
async def get_item_price(item_code: str, price_list: str | None = None) -> list[dict]:
    """Get item prices from Item Price records (exact match on item_code).

    ⚠️ 對 item_code 做精確比對；空結果可能是 item_code 不存在或無價單。
    不確定品項代碼時，先用 `find_items(keyword=...)` 找正確的 item_code。

    Args:
        item_code: Item code to look up (exact match)
        price_list: Optional price list name to filter (e.g. "Standard Selling")
    """
    return await get_client().get_item_price(item_code, price_list=price_list)


@mcp.tool()
@friendly_errors
async def make_mapped_doc(method: str, source_name: str) -> dict:
    """Create a new document mapped from an existing one (document conversion).

    Common methods:
    - erpnext.selling.doctype.quotation.quotation.make_sales_order (Quotation → Sales Order)
    - erpnext.selling.doctype.sales_order.sales_order.make_delivery_note (Sales Order → Delivery Note)
    - erpnext.selling.doctype.sales_order.sales_order.make_sales_invoice (Sales Order → Sales Invoice)
    - erpnext.stock.doctype.delivery_note.delivery_note.make_sales_invoice (Delivery Note → Sales Invoice)
    - erpnext.buying.doctype.purchase_order.purchase_order.make_purchase_receipt (PO → Purchase Receipt)
    - erpnext.buying.doctype.purchase_order.purchase_order.make_purchase_invoice (PO → Purchase Invoice)

    Args:
        method: Dotted path of the mapping method
        source_name: Name/ID of the source document
    """
    return await get_client().make_mapped_doc(method, source_name)


@mcp.tool()
@friendly_errors
async def get_party_balance(party_type: str, party: str) -> Any:
    """Get outstanding balance for a Customer or Supplier.

    Args:
        party_type: "Customer" or "Supplier"
        party: Party name/ID
    """
    return await get_client().get_party_balance(party_type, party)


@mcp.tool()
@friendly_errors
async def get_stock_ledger(item_code: str | None = None, warehouse: str | None = None, limit: int = 50) -> list[dict]:
    """Get stock ledger entries (inventory transaction history; exact match on item_code).

    ⚠️ 對 item_code 做精確比對；查不到時改用 `find_items(keyword=...)` 找正確代碼。

    Args:
        item_code: Optional exact item code filter
        warehouse: Optional warehouse filter
        limit: Max records to return (default 50)
    """
    return await get_client().get_stock_ledger(item_code=item_code, warehouse=warehouse, limit=limit)


@mcp.tool()
@friendly_errors
async def find_items(
    keyword: str,
    item_group: str | None = None,
    brand: str | None = None,
    include_disabled: bool = False,
    limit: int = 20,
) -> list[dict]:
    """Fuzzy-search Item across name / item_name / item_code (OR like %keyword%).

    用途：把使用者口語化的關鍵字（原廠型號、品名片段、部分代碼）解析成系統實際的
    `item_code`。ERPNext 的 Item code 常加公司前綴（例如 `CTOS-KV-N40DT`），
    用原廠型號 `KV-N40DT` 直接查 `get_stock_balance` 會找不到——此工具負責橋接。

    建議用法：先 `find_items` 確認 item_code，再 `get_stock_balance` / `get_item_price` /
    `get_stock_ledger`。或者直接用 `get_item_details(keyword=...)` 一次取完。

    Args:
        keyword: 搜尋關鍵字（會做 `like %keyword%` 比對 name / item_name / item_code）
        item_group: 可選的 item_group 過濾
        brand: 可選的品牌過濾
        include_disabled: 是否包含已停用品項（預設 False）
        limit: 最多回傳幾筆（預設 20）

    Returns:
        List of {name, item_code, item_name, item_group, brand, stock_uom, disabled, has_variants}
    """
    return await get_client().find_items(
        keyword=keyword,
        item_group=item_group,
        brand=brand,
        include_disabled=include_disabled,
        limit=limit,
    )


@mcp.tool()
@friendly_errors
async def get_item_details(
    name: str | None = None,
    keyword: str | None = None,
    warehouse: str | None = None,
    price_list: str | None = None,
) -> dict:
    """Get full item info (master + stock balance + prices) by exact name or fuzzy keyword.

    一次回完整資料，避免 AI 為了查一個品項要連打 3~4 支工具。
    解析順序：
    1. 若提供 `name` → 直接 get
    2. 若提供 `keyword` → 先試精確 name；找不到 fallback `find_items` 取第 1 筆候選

    Args:
        name: 精確 Item name/item_code（例如 "CTOS-KV-N40DT"）
        keyword: 模糊關鍵字（例如 "KV-N40DT"）；name 沒命中時用
        warehouse: 可選的倉庫過濾（套用在 stock balance）
        price_list: 可選的價單過濾（套用在 prices）

    Returns:
        {
          "item": {name, item_code, item_name, item_group, brand, stock_uom, ...},
          "stock": [{item_code, warehouse, actual_qty, projected_qty, ...}, ...],
          "prices": [{item_code, price_list, price_list_rate, currency, uom}, ...],
          "other_candidates": [...]   // 其他 fuzzy 候選（如果有的話）
        }
        或 {"error": "...", "hint": "..."}（找不到時）
    """
    return await get_client().get_item_details(
        name=name, keyword=keyword, warehouse=warehouse, price_list=price_list,
    )


# ── File Operations ─────────────────────────────────


@mcp.tool()
@friendly_errors
async def upload_file_from_url(
    file_url: str,
    filename: str | None = None,
    attached_to_doctype: str | None = None,
    attached_to_name: str | None = None,
    is_private: bool = True,
) -> dict:
    """Upload a file to ERPNext from a URL.

    Args:
        file_url: Source URL to fetch the file from
        filename: Optional name for the file (will be inferred from URL if not provided)
        attached_to_doctype: Optional DocType to attach file to
        attached_to_name: Optional document name to attach file to
        is_private: Whether file should be private (default True)

    Returns:
        File document with file_url and other metadata
    """
    return await get_client().upload_file_from_url(
        file_url=file_url,
        filename=filename,
        attached_to_doctype=attached_to_doctype,
        attached_to_name=attached_to_name,
        is_private=is_private,
    )


@mcp.tool()
@friendly_errors
async def upload_file(
    file_path: str,
    filename: str | None = None,
    attached_to_doctype: str | None = None,
    attached_to_name: str | None = None,
    is_private: bool = True,
) -> dict:
    """Upload a local file to ERPNext.

    Args:
        file_path: Local file path to upload (e.g. "/mnt/nas/files/report.pdf")
        filename: Optional name for the uploaded file (defaults to original filename)
        attached_to_doctype: Optional DocType to attach file to (e.g. "Project", "Item")
        attached_to_name: Optional document name to attach file to (e.g. "PROJ-0001")
        is_private: Whether file should be private (default True)

    Returns:
        File document with file_url and other metadata
    """
    from pathlib import Path

    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"File not found: {file_path}")

    # 讀取檔案內容
    file_content = path.read_bytes()

    # 使用原始檔名或指定的檔名
    upload_filename = filename or path.name

    return await get_client().upload_file(
        file_content=file_content,
        filename=upload_filename,
        attached_to_doctype=attached_to_doctype,
        attached_to_name=attached_to_name,
        is_private=is_private,
    )


@mcp.tool()
@friendly_errors
async def list_files(
    attached_to_doctype: str | None = None,
    attached_to_name: str | None = None,
    is_private: bool | None = None,
    limit: int = 20,
) -> list[dict]:
    """List files in ERPNext, optionally filtered by attachment.

    Args:
        attached_to_doctype: Filter by DocType (e.g. "Project", "Item")
        attached_to_name: Filter by document name (e.g. "PROJ-0001")
        is_private: Filter by privacy (True=private, False=public, None=all)
        limit: Max number of files to return (default 20)

    Returns:
        List of File documents with name, file_name, file_url, file_size, etc.
    """
    return await get_client().list_files(
        attached_to_doctype=attached_to_doctype,
        attached_to_name=attached_to_name,
        is_private=is_private,
        limit=limit,
    )


@mcp.tool()
@friendly_errors
async def get_file_url(file_name: str) -> str:
    """Get the full download URL for a file.

    Args:
        file_name: The File document name (e.g. "abc123.pdf" or the hash-based name)

    Returns:
        Full URL to download the file
    """
    return await get_client().get_file_url(file_name)


# @mcp.tool()
# @friendly_errors
# async def download_file(file_name: str) -> dict:
#     """Download a file's content from ERPNext.

#     Args:
#         file_name: The File document name

#     Returns:
#         Dict with 'content_base64' (file content as base64) and 'filename' (original filename)
#     """
#     import base64
#     content, filename = await get_client().download_file(file_name)
#     return {
#         "content_base64": base64.b64encode(content).decode("utf-8"),
#         "filename": filename,
#     }
@mcp.tool()
@friendly_errors
async def download_file(
    file_name: str,
    max_bytes: int = 20 * 1024 * 1024,
) -> dict:
    """Download a file's content from ERPNext (base64-encoded).

    For files larger than the limit, use `get_file_url()` instead and
    open the URL in a browser — base64 responses that big will overflow
    the model's context window.

    Args:
        file_name: The File document name
        max_bytes: Maximum file size to download (default 20 MB)
    """
    import base64
    content, filename = await get_client().download_file(file_name, max_bytes=max_bytes)
    return {
        "content_base64": base64.b64encode(content).decode("utf-8"),
        "filename": filename,
        "size_bytes": len(content),
    }

# ── Supplier/Customer Details ──────────────────────────


@mcp.tool()
@friendly_errors
async def get_supplier_details(name: str | None = None, keyword: str | None = None) -> dict:
    """Get complete supplier details including address, phone, and contacts.

    Args:
        name: Exact supplier name (e.g. "SF0009-2 - 永心企業社")
        keyword: Search keyword to find supplier (e.g. "永心", "健保局")

    Returns:
        Dict with supplier info, address (phone/fax), and contacts (our purchaser + their contacts)
    """
    client = get_client()

    # Find supplier
    if name:
        supplier = await client.get_doc("Supplier", name)
    elif keyword:
        # 先搜尋 name 欄位
        suppliers = await client.get_list(
            "Supplier",
            fields=["name", "supplier_name", "supplier_group", "country", "custom_alias"],
            filters={"name": ["like", f"%{keyword}%"]},
            limit_page_length=1,
        )
        # 找不到則搜尋 custom_alias 欄位（別名）
        if not suppliers:
            suppliers = await client.get_list(
                "Supplier",
                fields=["name", "supplier_name", "supplier_group", "country", "custom_alias"],
                filters={"custom_alias": ["like", f"%{keyword}%"]},
                limit_page_length=1,
            )
        if not suppliers:
            return {"error": f"找不到關鍵字「{keyword}」的供應商"}
        supplier = await client.get_doc("Supplier", suppliers[0]["name"])
    else:
        return {"error": "請提供 name 或 keyword"}

    supplier_name = supplier.get("name")

    # Get address (phone/fax)
    # Address title format: "代碼 地址", e.g. "SF0009-2 地址"
    code = supplier_name.split(" - ")[0] if " - " in supplier_name else supplier_name
    addresses = await client.get_list(
        "Address",
        fields=["address_title", "address_line1", "city", "pincode", "phone", "fax"],
        filters={"address_title": ["like", f"%{code}%"]},
        limit_page_length=5,
    )

    # Get contacts via Dynamic Link
    contacts = await client.get_list(
        "Contact",
        fields=["name", "first_name", "designation", "phone", "mobile_no", "email_id"],
        filters=[["Dynamic Link", "link_name", "=", supplier_name]],
        limit_page_length=50,
    )

    # Categorize contacts
    # 有 designation 的是我們的人（採購人員/業務人員），沒有的是對方的聯絡人
    our_contacts = []
    their_contacts = []
    for c in contacts:
        contact_info = {
            "name": c.get("first_name") or c.get("name"),
            "designation": c.get("designation") or "",
            "phone": c.get("phone") or c.get("mobile_no") or "",
            "email": c.get("email_id") or "",
        }
        if c.get("designation"):
            our_contacts.append(contact_info)
        else:
            their_contacts.append(contact_info)

    return {
        "supplier": {
            "name": supplier_name,
            "alias": supplier.get("custom_alias") or "",
            "group": supplier.get("supplier_group"),
            "country": supplier.get("country"),
            "currency": supplier.get("default_currency"),
        },
        "address": addresses[0] if addresses else None,
        "our_contacts": our_contacts,
        "their_contacts": their_contacts,
    }


@mcp.tool()
@friendly_errors
async def get_customer_details(name: str | None = None, keyword: str | None = None) -> dict:
    """Get complete customer details including address, phone, and contacts.

    Args:
        name: Exact customer name (e.g. "CM0001 - 正達工程股份有限公司")
        keyword: Search keyword to find customer (e.g. "正達")

    Returns:
        Dict with customer info, address (phone/fax), and contacts (our sales + their contacts)
    """
    client = get_client()

    # Find customer
    if name:
        customer = await client.get_doc("Customer", name)
    elif keyword:
        # 先搜尋 name 欄位
        customers = await client.get_list(
            "Customer",
            fields=["name", "customer_name", "customer_group", "territory", "custom_alias"],
            filters={"name": ["like", f"%{keyword}%"]},
            limit_page_length=1,
        )
        # 找不到則搜尋 custom_alias 欄位（別名）
        if not customers:
            customers = await client.get_list(
                "Customer",
                fields=["name", "customer_name", "customer_group", "territory", "custom_alias"],
                filters={"custom_alias": ["like", f"%{keyword}%"]},
                limit_page_length=1,
            )
        if not customers:
            return {"error": f"找不到關鍵字「{keyword}」的客戶"}
        customer = await client.get_doc("Customer", customers[0]["name"])
    else:
        return {"error": "請提供 name 或 keyword"}

    customer_name = customer.get("name")

    # Get address (phone/fax)
    code = customer_name.split(" - ")[0] if " - " in customer_name else customer_name
    addresses = await client.get_list(
        "Address",
        fields=["address_title", "address_line1", "city", "pincode", "phone", "fax"],
        filters={"address_title": ["like", f"%{code}%"]},
        limit_page_length=5,
    )

    # Get contacts via Dynamic Link
    contacts = await client.get_list(
        "Contact",
        fields=["name", "first_name", "designation", "phone", "mobile_no", "email_id"],
        filters=[["Dynamic Link", "link_name", "=", customer_name]],
        limit_page_length=50,
    )

    # Categorize contacts
    # 有 designation 的是我們的人（採購人員/業務人員），沒有的是對方的聯絡人
    our_contacts = []
    their_contacts = []
    for c in contacts:
        contact_info = {
            "name": c.get("first_name") or c.get("name"),
            "designation": c.get("designation") or "",
            "phone": c.get("phone") or c.get("mobile_no") or "",
            "email": c.get("email_id") or "",
        }
        if c.get("designation"):
            our_contacts.append(contact_info)
        else:
            their_contacts.append(contact_info)

    return {
        "customer": {
            "name": customer_name,
            "alias": customer.get("custom_alias") or "",
            "group": customer.get("customer_group"),
            "territory": customer.get("territory"),
            "currency": customer.get("default_currency"),
        },
        "address": addresses[0] if addresses else None,
        "our_contacts": our_contacts,
        "their_contacts": their_contacts,
    }
# ── Doc info & inspection ─────────────────────────────


@mcp.tool()
@friendly_errors
async def get_doc_info(doctype: str, name: str) -> dict:
    """Get summary info about a document: status, workflow_state, assignees,
    comment/attachment counts, timestamps, tags.

    Much cheaper than `get_document` when you only need metadata. Use this to
    answer "who's working on this?", "is it submitted?", "has it been touched
    recently?" without pulling child tables.

    Args:
        doctype: ERPNext DocType name
        name: Document name/ID
    """
    return await get_client().get_doc_info(doctype, name)


@mcp.tool()
@friendly_errors
async def get_linked_docs(doctype: str, name: str) -> dict:
    """Check which submitted documents link to this one.

    Call before `delete_document` to know whether ERPNext will block the
    delete. `can_delete: true` means no submitted links exist.

    Args:
        doctype: ERPNext DocType name
        name: Document name/ID
    """
    return await get_client().get_linked_docs(doctype, name)


@mcp.tool()
@friendly_errors
async def get_timeline(doctype: str, name: str, limit: int = 50) -> dict:
    """Get a document's full history: comments, version changes, emails.

    Returns `{comments, versions, communications}`. Use this to answer
    "what happened with this document?" or "who changed the price?".
    Version entries include field-level old→new diffs in `data.changed`.

    Args:
        doctype: ERPNext DocType name
        name: Document name/ID
        limit: Max records per category (default 50)
    """
    return await get_client().get_timeline(doctype, name, limit=limit)


@mcp.tool()
@friendly_errors
async def add_comment(doctype: str, name: str, text: str) -> dict:
    """Add a comment to a document (visible in the form's Activity timeline).

    Args:
        doctype: ERPNext DocType name
        name: Document name/ID
        text: Comment body (plain text or HTML)
    """
    return await get_client().add_comment(doctype, name, text)


# ── Workflow ──────────────────────────────────────────


@mcp.tool()
@friendly_errors
async def get_workflow_actions(doctype: str, name: str) -> list[dict]:
    """List workflow transitions available for a document.

    Each entry: `{action, next_state, allowed}`. `allowed=false` means the
    action exists but the current user / doc state blocks it.

    Call before `apply_workflow` so you know which actions are valid, and
    only surface the ones with `allowed: true`.

    Args:
        doctype: ERPNext DocType name
        name: Document name/ID
    """
    return await get_client().get_workflow_actions(doctype, name)


@mcp.tool()
@friendly_errors
async def apply_workflow(doctype: str, name: str, action: str) -> dict:
    """Apply a workflow action (e.g. "Approve", "Reject", "Send to Finance").

    The doc's `workflow_state` will change on success. Use
    `get_workflow_actions` first to confirm `action` is valid and allowed.

    Args:
        doctype: ERPNext DocType name
        name: Document name/ID
        action: Workflow action label, exactly as returned by
            `get_workflow_actions` (case-sensitive)
    """
    return await get_client().apply_workflow(doctype, name, action)


# ── Permissions & defaults ────────────────────────────


@mcp.tool()
@friendly_errors
async def get_permissions(doctype: str, doc_name: str | None = None) -> dict:
    """Check the current user's effective permissions on a DocType.

    Returns `{read, write, create, submit, cancel, delete, amend}` → bool.
    If `doc_name` is given, evaluates against that specific document
    (respects owner / workflow-state rules that override role permissions).

    Args:
        doctype: ERPNext DocType name
        doc_name: Optional document name for per-doc evaluation
    """
    return await get_client().get_permissions(doctype, doc_name=doc_name)


@mcp.tool()
@friendly_errors
async def get_defaults() -> dict:
    """Get system-wide defaults: company, currency, warehouse, price lists, fiscal year.

    Call once at the start of a session so you don't have to ask the user
    "which company?", "which price list?", "which warehouse?" — the answers
    are usually here.
    """
    return await get_client().get_defaults()


# ── Field-level fast path ─────────────────────────────


@mcp.tool()
@friendly_errors
async def get_value(
    doctype: str,
    name: str,
    field: str | list[str],
) -> dict:
    """Fetch one (or several) fields from a document — much cheaper than get_document.

    Args:
        doctype: ERPNext DocType name
        name: Document name/ID
        field: Field name (string) or list of field names

    Returns:
        `{fieldname: value, ...}` — only the requested fields.
    """
    return await get_client().get_value(doctype, name, field)


@mcp.tool()
@friendly_errors
async def set_value(
    doctype: str,
    name: str,
    field: str | dict[str, Any],
    value: Any = None,
) -> dict:
    """Update one (or several) fields on a document — much cheaper than update_document.

    Single field: `set_value("Sales Order", "SO-0042", "delivery_date", "2025-01-15")`
    Multiple fields: `set_value("Sales Order", "SO-0042", {"delivery_date": "...", "po_no": "X"})`

    Skips the fetch-modify-save cycle, so no stale-modified errors.

    Args:
        doctype: ERPNext DocType name
        name: Document name/ID
        field: Field name (string) OR dict of `{fieldname: new_value}`
        value: New value for the single field (ignored when `field` is a dict)
    """
    return await get_client().set_value(doctype, name, field, value)


# ── Rename & bulk operations ──────────────────────────


@mcp.tool()
@friendly_errors
async def rename_document(
    doctype: str,
    old_name: str,
    new_name: str,
    merge: bool = False,
) -> dict:
    """Rename a document. All links to old_name are rewritten automatically.

    Args:
        doctype: ERPNext DocType name
        old_name: Current document name/ID
        new_name: New document name/ID
        merge: If True and new_name already exists, merge old into new
    """
    return await get_client().rename_document(
        doctype, old_name, new_name, merge=merge,
    )


@mcp.tool()
@friendly_errors
async def bulk_create(
    doctype: str,
    items: str,
    concurrency: int = 1,
) -> dict:
    """Create many documents of the same DocType in one call.

    Returns per-row `{index, ok, name | error}` so partial failures are visible.
    `succeeded` and `failed` counts at the top level.

    Args:
        doctype: ERPNext DocType name
        items: JSON string — array of dicts, one per new doc, e.g.
            '[{"item_code": "X", "item_name": "Y"}, ...]'
        concurrency: 1 = sequential (safe); >1 = parallel (capped at N)
    """
    parsed = json.loads(items) if isinstance(items, str) else items
    if not isinstance(parsed, list):
        return {"error": True, "message": "items must be a JSON array of objects"}
    return await get_client().bulk_create(
        doctype, parsed, concurrency=concurrency,
    )
    
@mcp.tool()
@friendly_errors
async def find_docs(
    doctype: str,
    keyword: str,
    search_fields: list[str] | None = None,
    filters: str | None = None,
    limit: int = 20,
) -> list[dict]:
    """Fuzzy-search documents of any DocType across one or more fields.

    Runs `OR (field1 LIKE %kw% OR field2 LIKE %kw% ...)` in one query. Use
    this whenever the user gives a partial / human-typed identifier and you
    need the exact `name` before calling `get_document`, `get_stock_balance`,
    `set_value`, etc.

    Common (doctype, search_fields) pairs:
      - Item     → ["item_name", "item_code"]
      - Customer → ["customer_name"]
      - Supplier → ["supplier_name"]
      - Sales Order / Sales Invoice → ["name", "customer"]  (name is already the doc ID)
      - Employee → ["employee_name", "name"]
      - Lead     → ["lead_name", "company_name"]

    Returns matching docs (default fields = name + search_fields).

    Args:
        doctype: DocType to search in (e.g. "Item", "Customer")
        keyword: text to search for (case-insensitive, partial match)
        search_fields: fields to OR-match against. Defaults to ["name"].
            Almost always should include a human-readable field like
            item_name / customer_name, not just "name".
        filters: optional JSON string of AND filters. Either
            '{"status":"Open"}' or '[["Item","disabled","=",0]]'
        limit: max rows (default 20)

    Returns:
        List of dicts matching the docs. Empty list means no matches.
    """
    f = json.loads(filters) if filters else None
    return await get_client().find_docs(
        doctype=doctype,
        keyword=keyword,
        search_fields=search_fields,
        filters=f,
        limit=limit,
    )

def main():
    """Run the MCP server over Streamable HTTP.

    The endpoint defaults to http://127.0.0.1:9000/mcp.  These settings can
    be overridden for a container or a reverse-proxy deployment with
    ERPNEXT_MCP_HOST, ERPNEXT_MCP_PORT (or PORT), and ERPNEXT_MCP_PATH.
    """
    host = os.environ.get("ERPNEXT_MCP_HOST", "127.0.0.1")
    port = int(os.environ.get("ERPNEXT_MCP_PORT", os.environ.get("PORT", "8000")))
    path = os.environ.get("ERPNEXT_MCP_PATH", "/mcp")
    if not path.startswith("/"):
        path = f"/{path}"

    if os.environ.get("ERPNEXT_MCP_TRANSPORT") == "stdio":
     mcp.run("stdio")
    else:
     mcp.run("streamable-http", host=host, port=port, path=path)


if __name__ == "__main__":
    main()
