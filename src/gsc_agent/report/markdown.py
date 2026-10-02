"""Markdown report. Rule findings and model output stay in separate sections."""

from __future__ import annotations

from gsc_agent.analysis.engine import AnalysisResult, Opportunity
from gsc_agent.agent.loop import AgentResult
from gsc_agent.util import fmt_num, property_kind

DISCLAIMER = "尚未接入詢盤／成交數據時，無法確認哪些關鍵字實際帶來客戶。"


def render_report(result: AnalysisResult, agent_result: AgentResult | None = None) -> str:
    kind = "網域資源（sc-domain）" if result.property_type == "domain" else "網址前綴資源（URL-prefix）"
    lines: list[str] = [
        "# Search Console 本地分析報告",
        "",
        f"- 資源：{safe_inline(result.site_url)}",
        f"- 資源類型：{kind}",
        f"- 日期：{result.start_date} 至 {result.end_date}（太平洋時間，與 Search Console API 一致，未經本機時區換算）",
        f"- 目標國家：{result.target_country}",
        "- 搜尋類型：web",
        "- 數據狀態：final（只使用已完成處理的數據）",
        "",
        "## 限制",
        "",
        DISCLAIMER,
        "",
        "Search Console 只能顯示搜尋表現。本報告沒有詢盤、電話、表單或成交數據，因此不能指出哪些關鍵字實際帶來客戶。",
        "",
        "網站總覽只來自資料集 property_daily（dimensions=date、aggregationType=byProperty）。"
        "查詢、頁面、國家與 query+page+country 明細是各自獨立的聚合，不可把它們的曝光或點擊相加當作網站總數。",
        "",
        result.unreturned_query_note,
        "",
        "## 覆蓋範圍",
        "",
        *_coverage_lines(result),
        "",
        "## 數據事實",
        "",
        "以下數字來自本地資料庫，不是 AI 推測。",
        "",
        "### 網站總覽（資料集 property_daily）",
        "",
        _metrics_line("網站總覽", result.site_totals),
        "",
        "### 國家或地區（資料集 country，byProperty）",
        "",
        "國家分佈只用這個資料集。它的合計不一定等於明細資料集。",
        "",
        *_country_lines(result),
        "",
        "### 品牌與非品牌（資料集 query，僅含 API 有返回的查詢）",
        "",
        *_brand_lines(result),
        "",
        "### 頁面類型（資料集 page，byPage）",
        "",
        "頁面資料集使用 byPage，不能與 property 總覽直接對減後當成遺失流量。",
        "",
        *_page_lines(result),
        "",
        "### 其他資料集的合計（不是網站總覽）",
        "",
        _metrics_line("query 返回列合計", result.returned_query_totals),
        _metrics_line("page 返回列合計", result.page_totals),
        _metrics_line("country 返回列合計", result.country_totals),
        _metrics_line("query_page_country 明細合計", result.detail_totals),
        "",
        "## 規則判斷",
        "",
        "這一節依設定檔的門檻產生，屬於規則判斷，不是數據事實本身，也不是 AI 推測。",
        "平均排名不是固定名次。不同排名區間使用各自的點擊上限，沒有一條共用的 CTR 基準。",
        "",
        "### 值得人工驗證的服務頁機會",
        "",
    ]
    lines.extend(_opportunity_blocks(result.ranked_opportunities, empty="這段範圍沒有同時符合國家、意圖、頁面、排名區間與樣本門檻的服務頁機會。"))
    lines.extend(["", "### 值得人工檢查的內部連結", ""])
    lines.extend(_opportunity_blocks(result.ranked_links, empty="這段範圍沒有達到樣本門檻、且文章與服務頁共享查詢的連結檢查項。"))
    lines.extend(
        [
            "",
            "### 低信心附錄（樣本不足或不確定，不排名）",
            "",
            "以下項目不參與排名。曝光或點擊不足、或意圖混合時，不強行排序。",
            "",
        ]
    )
    lines.extend(
        _opportunity_blocks(
            [*result.low_confidence, *result.low_confidence_links],
            empty="沒有低信心項目。",
        )
    )
    if agent_result is not None:
        lines.extend(["", *render_agent_section(agent_result)])
    lines.extend(["", "## 再次說明", "", DISCLAIMER, ""])
    return "\n".join(lines)


def render_agent_section(agent_result: AgentResult) -> list[str]:
    lines = [
        "## AI 推測（ReAct）",
        "",
        "以下由語言模型在工具結果之上產生，可能出錯。模型不能執行 SQL、shell，也不能修改資料庫。",
        "查詢字與網址被視為不可信資料，不會被當成工具指令。",
        "",
        f"- 問題：{agent_result.question}",
        f"- 停止原因：{agent_result.stopped_reason}",
        f"- 工具呼叫次數：{agent_result.tool_calls}",
        "",
        "### 過程",
        "",
    ]
    if not agent_result.steps:
        lines.append("沒有完成任何工具步驟。")
    for index, step in enumerate(agent_result.steps, start=1):
        lines.append(f"{index}. 判斷：{safe_text(step.thought)}")
        if step.action:
            lines.append(f"   - 選擇工具：{safe_text(step.action)}")
            lines.append(f"   - 結果：{safe_text(step.observation_preview)}")
            lines.append(f"   - 是否繼續：{'是' if step.continued else '否'}")
        else:
            lines.append("   - 產生結論，不再呼叫工具。")
    lines.extend(["", "### 結論", "", safe_text(agent_result.final) or "模型沒有產生結論。", ""])
    lines.append("### 證據與未核對數字")
    lines.append("")
    if agent_result.warnings:
        for warning in agent_result.warnings:
            lines.append(f"- {safe_text(warning)}")
    else:
        lines.append("- 結論裡的數字都能在工具輸出中找到。這仍不表示推論一定正確。")
    lines.append("")
    lines.append("AI 推測不能取代上一節的數據事實，也不能提供轉換率或成交結論。")
    return lines


def _coverage_lines(result: AnalysisResult) -> list[str]:
    lines = [f"要求的日期共 {len(result.expected_dates)} 日。沒有同步紀錄的日期是「尚未同步」，與「API 成功但沒有列」不同。"]
    for name, coverage in result.coverage.items():
        lines.append(
            f"- {name}：完成 {len(coverage.completed)}、空結果 {len(coverage.empty)}、"
            f"截斷 {len(coverage.truncated)}、失敗 {len(coverage.failed)}、尚未同步 {len(coverage.missing)}"
        )
        if coverage.truncated:
            lines.append(f"  - 截斷日期：{', '.join(coverage.truncated)}。這些日子只含 API 按點擊返回的前列。")
        if coverage.failed:
            lines.append(f"  - 失敗日期：{', '.join(coverage.failed)}。失敗日保留舊列，但這次分析不採用。")
        if coverage.missing:
            lines.append(f"  - 尚未同步：{', '.join(coverage.missing)}")
    return lines


def _country_lines(result: AnalysisResult) -> list[str]:
    if not result.country_rows:
        return ["這個範圍沒有可採用的國家資料。"]
    lines = []
    for row in result.country_rows:
        share = row["impression_share_within_country_dataset"] * 100
        marker = "目標國家" if str(row["country"]).casefold() == result.target_country.casefold() else "其他地區"
        lines.append(
            f"- {row['country']}（{marker}）：點擊 {fmt_num(row['clicks'])}，曝光 {fmt_num(row['impressions'])}，"
            f"CTR {fmt_num(row['ctr'])}，平均排名 {fmt_num(row['position'])}，"
            f"占國家資料集曝光 {share:.1f}%。"
        )
    return lines


def _brand_lines(result: AnalysisResult) -> list[str]:
    labels = {
        "brand": "品牌詞",
        "non_brand": "非品牌詞",
        "brand_unknown": "品牌詞未設定，無法分類",
    }
    return [
        f"- {labels[row['brand_class']]}：查詢數 {row['query_count']}，點擊 {fmt_num(row['clicks'])}，"
        f"曝光 {fmt_num(row['impressions'])}，CTR {fmt_num(row['ctr'])}，平均排名 {fmt_num(row['position'])}。"
        for row in result.brand_rows
    ]


def _page_lines(result: AnalysisResult) -> list[str]:
    labels = {"service": "服務頁", "article": "文章頁", "other": "其他頁面"}
    return [
        f"- {labels[row['page_type']]}：頁面數 {row['page_count']}，點擊 {fmt_num(row['clicks'])}，"
        f"曝光 {fmt_num(row['impressions'])}，CTR {fmt_num(row['ctr'])}，平均排名 {fmt_num(row['position'])}。"
        for row in result.page_type_rows
    ]


def _metrics_line(label: str, metrics) -> str:
    return (
        f"- {label}：點擊 {fmt_num(metrics.clicks)}，曝光 {fmt_num(metrics.impressions)}，"
        f"CTR {fmt_num(metrics.ctr)}，平均排名 {fmt_num(metrics.position)}。"
    )


def _opportunity_blocks(items: list[Opportunity], *, empty: str) -> list[str]:
    if not items:
        return [empty]
    blocks: list[str] = []
    for index, item in enumerate(items, start=1):
        blocks.extend(
            [
                f"#### {index}. {category_label(item.category)}",
                "",
                f"- 查詢／查詢群：{safe_inline(item.query)}（MVP 每一列是單一查詢，不合併同義詞）",
                f"- 對應頁面：{safe_inline(item.page)}",
                f"- 相關頁面：{safe_inline(item.related_page) if item.related_page else '無'}",
                f"- 國家：{safe_inline(item.country)}",
                f"- 日期：{item.start_date} 至 {item.end_date}",
                f"- 點擊：{fmt_num(item.clicks)}",
                f"- 曝光：{fmt_num(item.impressions)}",
                f"- CTR：{fmt_num(item.ctr)}",
                f"- 平均排名：{fmt_num(item.position)}",
                f"- 機會類別：{item.category}",
                f"- 信心：{'列入人工驗證' if item.confidence == 'ranked' else '低信心，不排名'}",
                f"- 建議行動：{item.action}",
                f"- 證據：{item.evidence}",
                f"- 數據事實：{' '.join(item.facts)}",
                f"- 規則判斷：{' '.join(item.inferences)}",
                f"- 不確定性：{item.uncertainty}",
                f"- 如何驗證成效：{item.verification}",
                "",
            ]
        )
    return blocks


def category_label(category: str) -> str:
    return {
        "service_page_review": "服務頁人工驗證",
        "internal_link_review": "內部連結人工檢查",
    }.get(category, category)


def safe_text(value: str) -> str:
    return value.replace("\r", " ").replace("\n", " ")


def safe_inline(value: str) -> str:
    return "`" + safe_text(value).replace("`", "'") + "`"


def property_type_label(site_url: str) -> str:
    return "domain" if property_kind(site_url) == "domain" else "url_prefix"
