"""Command line entry point."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

import typer

from gsc_agent import __version__
from gsc_agent.agent.llm import prepare_llm
from gsc_agent.agent.loop import run_react
from gsc_agent.agent.tools import make_context
from gsc_agent.analysis.engine import analyze as analyze_property
from gsc_agent.auth.oauth import run_local_oauth, safe_exception_text
from gsc_agent.config import load_config, load_dotenv_file, resolve_config_path, resolve_runtime_paths
from gsc_agent.db.migrate import open_migrated
from gsc_agent.db.store import Store
from gsc_agent.demo.seed import DEMO_END, DEMO_SITE, DEMO_START, run_demo
from gsc_agent.doctor import doctor_failed, format_doctor, format_property_line, run_doctor
from gsc_agent.errors import GscAgentError, LLMNotConfigured, RemoteLLMNotAllowed
from gsc_agent.gsc.client import GoogleSearchConsoleClient
from gsc_agent.report.markdown import render_report
from gsc_agent.setup_llm import PLATFORMS, llm_is_ready, save_llm_settings
from gsc_agent.sync.syncer import Syncer
from gsc_agent.util import parse_calendar_date, redact

app = typer.Typer(add_completion=False, no_args_is_help=True, help="把你自己的 Search Console 數據留在本機，並標出值得人工驗證的 SEO 機會。")
def _config_opt():
    return typer.Option(None, "--config", help="TOML 設定檔。未指定時使用 GSC_AGENT_CONFIG 或 ./config.toml。")


def _db_opt():
    return typer.Option(None, "--db", help="覆寫 SQLite 路徑。")


def _load(config: Optional[Path], database: Optional[Path]):
    load_dotenv_file()
    path = config or resolve_config_path()
    if path is None:
        raise typer.BadParameter("找不到設定檔。請複製 config.example.toml 為 config.toml，或傳入 --config。")
    loaded = load_config(path)
    return loaded, resolve_runtime_paths(loaded, database_override=database)


def _store(path: Path):
    connection = open_migrated(path)
    return Store(connection), connection


def _report_path(site_url: str, start: str, end: str, output: Optional[Path]) -> Path:
    if output is not None:
        return output
    slug = re.sub(r"[^A-Za-z0-9]+", "-", site_url).strip("-").lower()[:80] or "property"
    return Path("reports") / f"{slug}-{start}-{end}.md"


def _write_report(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


@app.command()
def setup(
    config: Optional[Path] = _config_opt(),
    env_file: Path = typer.Option(Path(".env"), "--env-file", help="API key 寫入這個檔案。不要提交它。"),
    check: bool = typer.Option(False, "--check", help="只檢查是否已有網址、模型與 key，不詢問。"),
) -> None:
    """先選平台，再保存網址與模型。遠端平台的 key 只寫入 .env。"""
    config_path = config or resolve_config_path() or Path("config.toml")
    if check:
        raise typer.Exit(code=0 if llm_is_ready(config_path, env_file) else 2)
    typer.echo("選一個平台。不需要 OpenAI。")
    for index, platform in enumerate(PLATFORMS, start=1):
        typer.echo(f"{index}. {platform.label}")
    raw_choice = typer.prompt("輸入編號").strip()
    if not raw_choice.isdigit() or not 1 <= int(raw_choice) <= len(PLATFORMS):
        typer.echo("請輸入清單上的編號。沒有寫入。", err=True)
        raise typer.Exit(code=1)
    platform = PLATFORMS[int(raw_choice) - 1]
    try:
        if platform.kind == "ollama":
            host = typer.prompt("Ollama 網址", default=platform.base_url).strip()
            model = typer.prompt("模型名稱", default=platform.model).strip()
            save_llm_settings(config_path, env_file, base_url=host, model=model, provider="ollama")
            typer.echo("已改用本機 Ollama。這次不需要 API key，搜尋資料不會送到雲端。")
            typer.echo("請先在這台電腦安裝並啟動 Ollama，而且已經下載你填的模型。")
            return
        if platform.kind == "custom":
            base_url = typer.prompt("AI 網址（以 /v1 或相容路徑結尾）").strip()
            model = typer.prompt("模型名稱").strip()
            key_hint = platform.key_hint
        else:
            base_url = typer.prompt("AI 網址", default=platform.base_url).strip()
            model = typer.prompt("模型名稱", default=platform.model).strip()
            key_hint = platform.key_hint
        typer.echo(f"搜尋字、網址和指標會送到 {base_url}。")
        typer.echo(f"請貼上{key_hint}。輸入時不會顯示。")
        api_key = typer.prompt("API key", hide_input=True)
        confirm = typer.prompt("再輸入一次 API key", hide_input=True)
        if api_key != confirm:
            typer.echo("兩次 API key 不相同。沒有寫入。", err=True)
            raise typer.Exit(code=1)
        save_llm_settings(
            config_path,
            env_file,
            base_url=base_url,
            model=model,
            api_key=api_key,
            provider="openai_compatible",
        )
    except GscAgentError as exc:
        typer.echo(redact(str(exc)), err=True)
        raise typer.Exit(code=1) from None
    typer.echo(f"已寫入平台網址與模型到 {config_path}。")
    typer.echo(f"API key 已寫入 {env_file}，權限設為本人可讀。key 內容不會顯示。")


@app.command()
def doctor(
    config: Optional[Path] = _config_opt(),
    db: Optional[Path] = _db_opt(),
) -> None:
    """檢查 Python、資料庫、OAuth、token 與 Search Console 是否真的可呼叫。"""
    try:
        _loaded, paths = _load(config, db)
        checks = run_doctor(paths)
    except GscAgentError as exc:
        typer.echo(redact(str(exc)), err=True)
        raise typer.Exit(code=1) from None
    typer.echo(format_doctor(checks))
    if doctor_failed(checks):
        raise typer.Exit(code=1)


@app.command()
def auth(config: Optional[Path] = _config_opt()) -> None:
    """用本機瀏覽器授權 Search Console 唯讀範圍。"""
    try:
        _loaded, paths = _load(config, None)
        run_local_oauth(paths.client_secrets, paths.token)
    except GscAgentError as exc:
        typer.echo(safe_exception_text(exc), err=True)
        raise typer.Exit(code=1) from None
    typer.echo(f"授權完成。token 已儲存到 {paths.token}。token 內容不會顯示。")


@app.command()
def properties(config: Optional[Path] = _config_opt()) -> None:
    """列出目前帳戶可讀取的 URL-prefix 與 sc-domain 資源。"""
    try:
        from gsc_agent.auth.oauth import load_credentials

        _loaded, paths = _load(config, None)
        credentials = load_credentials(paths.token)
        sites = GoogleSearchConsoleClient(credentials).list_sites()
    except GscAgentError as exc:
        typer.echo(redact(str(exc)), err=True)
        raise typer.Exit(code=1) from None
    if not sites:
        typer.echo("API 已連接，但此帳戶沒有可讀取的 Search Console 資源。")
        raise typer.Exit(code=1)
    for site in sites:
        typer.echo(format_property_line(site))


@app.command()
def sync(
    property_url: str = typer.Option(..., "--property", help="Search Console 資源，例如 https://example.com/ 或 sc-domain:example.com。"),
    start: str = typer.Option(..., "--start", help="開始日期 YYYY-MM-DD，依 Search Console 的太平洋時間。"),
    end: str = typer.Option(..., "--end", help="結束日期 YYYY-MM-DD，含當日。"),
    config: Optional[Path] = _config_opt(),
    db: Optional[Path] = _db_opt(),
) -> None:
    """同步 web 搜尋的五個獨立資料集。不會把不同維度加總成網站總數。"""
    try:
        parse_calendar_date(start)
        parse_calendar_date(end)
        _loaded, paths = _load(config, db)
        from gsc_agent.auth.oauth import load_credentials

        store, connection = _store(paths.database)
        try:
            client = GoogleSearchConsoleClient(load_credentials(paths.token))
            result = Syncer(store, client).sync(property_url, start, end)
        finally:
            connection.close()
    except GscAgentError as exc:
        typer.echo(redact(str(exc)), err=True)
        raise typer.Exit(code=1) from None
    except ValueError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from None
    for item in result.datasets:
        line = f"{item.dataset}: {item.status}，寫入 {item.rows_fetched} 列"
        if item.truncated:
            line += "，含截斷"
        if item.error_class:
            line += f"，錯誤 {item.error_class}"
        typer.echo(line)
    if not result.ok:
        raise typer.Exit(code=1)


@app.command()
def analyze(
    property_url: str = typer.Option(..., "--property"),
    start: str = typer.Option(..., "--start"),
    end: str = typer.Option(..., "--end"),
    target_country: Optional[str] = typer.Option(None, "--target-country", help="ISO 3166-1 alpha-3，例如 HKG。"),
    config: Optional[Path] = _config_opt(),
    db: Optional[Path] = _db_opt(),
) -> None:
    """用設定檔規則分析本地資料。不需要 LLM。"""
    try:
        loaded, paths = _load(config, db)
        store, connection = _store(paths.database)
        try:
            result = analyze_property(store, loaded, property_url, start, end, target_country=target_country)
        finally:
            connection.close()
    except GscAgentError as exc:
        typer.echo(redact(str(exc)), err=True)
        raise typer.Exit(code=1) from None
    except ValueError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from None
    typer.echo(
        f"網站總覽（property_daily）點擊 {result.site_totals.clicks:.0f}，"
        f"曝光 {result.site_totals.impressions:.0f}。"
    )
    typer.echo(f"列入人工驗證的服務頁機會：{len(result.ranked_opportunities)}")
    typer.echo(f"列入人工檢查的內部連結：{len(result.ranked_links)}")
    typer.echo(f"低信心、不排名：{len(result.low_confidence) + len(result.low_confidence_links)}")
    typer.echo("這不是詢盤或成交結論。若要完整報告，請執行 gsc-agent report。")


@app.command()
def report(
    property_url: str = typer.Option(..., "--property"),
    start: str = typer.Option(..., "--start"),
    end: str = typer.Option(..., "--end"),
    target_country: Optional[str] = typer.Option(None, "--target-country"),
    output: Optional[Path] = typer.Option(None, "--output", help="Markdown 輸出路徑。"),
    config: Optional[Path] = _config_opt(),
    db: Optional[Path] = _db_opt(),
) -> None:
    """把規則分析寫成本地 Markdown。不需要 LLM。"""
    try:
        loaded, paths = _load(config, db)
        store, connection = _store(paths.database)
        try:
            result = analyze_property(store, loaded, property_url, start, end, target_country=target_country)
        finally:
            connection.close()
        text = render_report(result)
        destination = _report_path(property_url, start, end, output)
        _write_report(destination, text)
    except GscAgentError as exc:
        typer.echo(redact(str(exc)), err=True)
        raise typer.Exit(code=1) from None
    except ValueError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from None
    typer.echo(f"已寫入 {destination}")
    typer.echo(text)


@app.command()
def agent(
    property_url: str = typer.Option(..., "--property"),
    start: str = typer.Option(..., "--start"),
    end: str = typer.Option(..., "--end"),
    target_country: Optional[str] = typer.Option(None, "--target-country"),
    output: Optional[Path] = typer.Option(None, "--output"),
    allow_remote_llm: bool = typer.Option(False, "--allow-remote-llm", help="明確允許把查詢字、網址與指標送到遠端 LLM。"),
    config: Optional[Path] = _config_opt(),
    db: Optional[Path] = _db_opt(),
) -> None:
    """執行有工具呼叫上限的 ReAct。沒有 LLM 時請改用 analyze。"""
    try:
        loaded, paths = _load(config, db)
        llm = prepare_llm(loaded, allow_remote_flag=allow_remote_llm)
        if getattr(llm, "remote", False):
            typer.echo("警告：查詢字、網址與 Search Console 指標將傳送到遠端 LLM。")
        else:
            typer.echo("使用本機 LLM。這次執行不會把 Search Console 數據送到雲端。")
        store, connection = _store(paths.database)
        try:
            result = analyze_property(store, loaded, property_url, start, end, target_country=target_country)
            ctx = make_context(store, loaded, property_url, start, end, target_country=target_country)
            agent_result = run_react(llm, ctx)
        finally:
            connection.close()
        text = render_report(result, agent_result)
        destination = _report_path(property_url, start, end, output)
        _write_report(destination, text)
    except (LLMNotConfigured, RemoteLLMNotAllowed) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=2) from None
    except GscAgentError as exc:
        typer.echo(redact(str(exc)), err=True)
        raise typer.Exit(code=1) from None
    except ValueError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from None
    typer.echo(f"已寫入 {destination}")
    typer.echo(text)


@app.command()
def demo(directory: Path = typer.Option(Path("demo-data"), "--dir", help="虛構資料與報告的輸出目錄。")) -> None:
    """用虛構數據跑分析與報告。不需要 Google 憑證或 LLM。"""
    report_path = run_demo(directory)
    typer.echo(f"已用虛構資源 {DEMO_SITE} 產生報告：{report_path}")
    typer.echo(f"示範日期：{DEMO_START} 至 {DEMO_END}。這不是真實網站數據。")
    typer.echo(report_path.read_text(encoding="utf-8"))


@app.command()
def version() -> None:
    """顯示版本。"""
    typer.echo(__version__)


def main() -> None:
    app()


if __name__ == "__main__":
    main()
