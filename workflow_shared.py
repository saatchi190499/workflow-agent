"""Shared workflow table utilities.

This file intentionally has no Django / HTTP dependencies so it can be used by:
- prodcast-worker (celery worker execution)
- workflow-agent (local FastAPI runner)

Single source of truth for:
- OutputsTable/InputsTable structure helpers
- outputs_config componentId resolution
- converting OutputsTable -> records
- shared Teams alert client for workflow runtime
"""

from __future__ import annotations

import json
import ssl
import urllib.error
import urllib.request


def _norm(s) -> str:
    if s is None:
        return ""
    return str(s).strip().lower()


def infer_object_type_from_table_name(name: str | None) -> str | None:
    if not isinstance(name, str):
        return None
    base = name
    if base.endswith("OutputsTable"):
        base = base[: -len("OutputsTable")]
    if base.endswith("InputsTable"):
        base = base[: -len("InputsTable")]
    base = base.strip()
    return base or None


def iter_table_rows(col: dict) -> list:
    """Best-effort row iteration for our AttrDict-based tables."""
    if not isinstance(col, dict):
        return []

    rows = col.get("_row_list")
    if isinstance(rows, list) and rows:
        return rows

    rows = col.get("Row")
    if isinstance(rows, list):
        return rows

    if isinstance(rows, dict):
        # Deduplicate by object identity; dict may contain both int+name keys.
        out = []
        seen = set()
        for row in rows.values():
            if id(row) in seen:
                continue
            seen.add(id(row))
            out.append(row)
        return out

    return []


def resolve_table_and_type(table, object_type=None):
    """Return (table_dict_or_None, object_type_or_None)."""
    inferred = None
    if isinstance(table, str):
        inferred = infer_object_type_from_table_name(table)
        table = globals().get(table)

    if isinstance(table, dict) and not object_type:
        inferred = (
            table.get("_ObjectType")
            or table.get("ObjectType")
            or table.get("__object_type")
            or inferred
            or infer_object_type_from_table_name(table.get("_TableName") or table.get("__table_name"))
        )

    return (table if isinstance(table, dict) else None), (object_type or inferred)


def outputs_component_for(outputs_config: dict | None, object_type, prop) -> int | None:
    """Resolve component id from Workflow.outputs_config (tabs/columns)."""
    if not isinstance(outputs_config, dict):
        return None
    tabs = outputs_config.get("tabs")
    if not isinstance(tabs, list):
        return None

    ot_key = _norm(object_type)
    prop_key = _norm(prop)

    for tab in tabs:
        if not isinstance(tab, dict):
            continue
        otype = tab.get("objectType") or tab.get("object_type")
        if _norm(otype) != ot_key:
            continue

        tab_comp = tab.get("componentId") or tab.get("component_id")
        for col in (tab.get("columns") or []):
            if not isinstance(col, dict):
                continue
            p = col.get("property")
            if _norm(p) != prop_key:
                continue
            c_id = col.get("componentId") or col.get("component_id") or tab_comp
            if c_id is None:
                return None
            try:
                return int(str(c_id))
            except Exception:
                return None

    return None


def workflow_instances_from_config(block: dict | None, object_type=None) -> list:
    if not isinstance(block, dict):
        return []
    tabs = block.get("tabs")
    out = []
    if isinstance(tabs, list):
        for tab in tabs:
            if not isinstance(tab, dict):
                continue
            otype = tab.get("objectType") or tab.get("object_type")
            if object_type is not None and str(otype) != str(object_type):
                continue
            for inst in (tab.get("instances") or []):
                if inst and inst not in out:
                    out.append(inst)
    return out


def workflow_properties_from_config(block: dict | None, object_type=None) -> list:
    if not isinstance(block, dict):
        return []
    tabs = block.get("tabs")
    out = []
    if isinstance(tabs, list):
        for tab in tabs:
            if not isinstance(tab, dict):
                continue
            otype = tab.get("objectType") or tab.get("object_type")
            if object_type is not None and str(otype) != str(object_type):
                continue
            for prop in (tab.get("properties") or []):
                if prop and prop not in out:
                    out.append(prop)
            for col in (tab.get("columns") or []):
                if not isinstance(col, dict):
                    continue
                prop = col.get("property")
                if prop and prop not in out:
                    out.append(prop)
    return out


def records_from_output_table(
    table: dict,
    *,
    object_type=None,
    outputs_config: dict | None = None,
    description=None,
    date_time=None,
) -> list[dict]:
    """Convert OutputsTable (dict) into record list ready to save."""
    records: list[dict] = []
    if not isinstance(table, dict):
        return records

    table_comp = table.get("_ComponentId") or table.get("ComponentId")

    for _, col in table.items():
        if not isinstance(col, dict):
            continue

        col_prop = col.get("ObjectTypeProperty")
        if not col_prop:
            continue

        col_comp = col.get("ComponentId") or table_comp or outputs_component_for(outputs_config, object_type, col_prop)

        for row in iter_table_rows(col):
            if not isinstance(row, dict):
                continue
            inst = row.get("ObjectInstance")
            if inst is None:
                continue
            for sample in (row.get("Sample") or []):
                if not isinstance(sample, dict):
                    continue
                value = sample.get("Value")
                if value is None:
                    continue
                records.append(
                    {
                        "component": col_comp,
                        "object_type": object_type,
                        "object_instance": inst,
                        "object_type_property": col_prop,
                        "value": value,
                        "date_time": sample.get("TimeOfSample") or date_time,
                        "description": description,
                    }
                )

    return records


def _teams_text(msg) -> str:
    if msg is None:
        return ""
    if isinstance(msg, str):
        return msg
    try:
        return json.dumps(msg, ensure_ascii=False, indent=2)
    except Exception:
        return str(msg)


def _teams_payload(msg, title: str = "Workflow Alert") -> dict:
    return {
        "type": "message",
        "attachments": [
            {
                "contentType": "application/vnd.microsoft.card.adaptive",
                "content": {
                    "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
                    "type": "AdaptiveCard",
                    "version": "1.4",
                    "body": [
                        {"type": "TextBlock", "text": str(title), "weight": "Bolder", "size": "Medium", "wrap": True},
                        {"type": "TextBlock", "text": _teams_text(msg), "wrap": True},
                    ],
                },
            }
        ],
    }


class TeamsClient:
    def __init__(self, ssl_verify: bool = True):
        self.ssl_verify = bool(ssl_verify)

    def send_alert(self, web_hook, msg, title: str = "Workflow Alert", timeout: float = 15, raise_on_error: bool = True):
        url = str(web_hook or "").strip()
        if not url:
            raise ValueError("web_hook is required")

        payload = _teams_payload(msg, title=title)
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=body,
            headers={"Content-Type": "application/json; charset=utf-8"},
            method="POST",
        )

        ssl_context = None if self.ssl_verify else ssl._create_unverified_context()

        try:
            with urllib.request.urlopen(req, timeout=float(timeout), context=ssl_context) as resp:
                status = int(getattr(resp, "status", 200) or 200)
                resp_body = resp.read().decode("utf-8", "ignore")
        except urllib.error.HTTPError as exc:
            status = int(getattr(exc, "code", 500) or 500)
            resp_body = (exc.read() or b"").decode("utf-8", "ignore")
            if raise_on_error:
                raise RuntimeError(f"Failed to send Teams alert: HTTP {status}: {resp_body}") from exc
            return {"ok": False, "status": status, "body": resp_body, "error": resp_body}
        except Exception as exc:
            if raise_on_error:
                raise RuntimeError(f"Failed to send Teams alert: {exc}") from exc
            return {"ok": False, "status": None, "body": "", "error": str(exc)}

        ok = 200 <= status < 300
        if (not ok) and raise_on_error:
            raise RuntimeError(f"Failed to send Teams alert: HTTP {status}: {resp_body}")
        return {"ok": ok, "status": status, "body": resp_body, "error": None if ok else resp_body}
