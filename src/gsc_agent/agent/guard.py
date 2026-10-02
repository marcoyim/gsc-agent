"""Allow-list for agent tools. Anything else is refused before it can run."""

from __future__ import annotations

from typing import Any

from gsc_agent.errors import DatabaseError

ALLOWED_TOOLS = frozenset(
    {
        "get_data_coverage",
        "get_property_summary",
        "get_top_queries",
        "get_page_queries",
        "compare_countries",
        "compare_periods",
        "get_opportunity_candidates",
    }
)

_FORBIDDEN_ARGUMENTS = frozenset({"sql", "query_sql", "statement", "shell", "command", "code"})


def wrap_untrusted(value: str) -> str:
    cleaned = value.replace("</untrusted_data>", "")
    return f"<untrusted_data>{cleaned}</untrusted_data>"


def execute_tool(name: object, arguments: object, ctx: Any) -> dict[str, Any]:
    """Run one allow-listed tool. Disallowed names and SQL arguments are not executed."""
    if not isinstance(name, str) or name not in ALLOWED_TOOLS:
        return {
            "executed": False,
            "error": "disallowed_tool",
            "message": "這個工具不在允許清單。Agent 不能執行 SQL、shell 或寫入資料庫。",
        }
    if not isinstance(arguments, dict):
        return {"executed": False, "error": "invalid_arguments", "message": "工具參數必須是物件。"}
    if _FORBIDDEN_ARGUMENTS.intersection(arguments):
        return {
            "executed": False,
            "error": "disallowed_argument",
            "message": "工具不接受 SQL、shell 或程式碼參數。",
        }
    from gsc_agent.agent import tools as tool_impl

    handler = getattr(tool_impl, name)
    try:
        return handler(ctx, arguments)
    except (ValueError, DatabaseError) as exc:
        return {"executed": False, "error": "invalid_arguments", "message": str(exc)}
