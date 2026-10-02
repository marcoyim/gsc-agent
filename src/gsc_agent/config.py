"""Load the TOML config. Thresholds live here, not in the analysis rules."""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from gsc_agent.errors import ConfigError
from gsc_agent.paths import RuntimePaths, default_client_secrets_path, default_database_path, default_token_path


def load_dotenv_file(path: Path | None = None) -> None:
    env_path = path or Path(".env")
    if not env_path.is_file():
        return
    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


@dataclass(frozen=True)
class PositionBand:
    min_position: float
    max_position: float
    max_clicks: float
    reason: str


@dataclass(frozen=True)
class ScoringConfig:
    min_impressions_for_ranking: int = 50
    low_confidence_impressions: int = 30
    min_article_clicks_for_link: int = 3
    position_bands: tuple[PositionBand, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class AnalysisConfig:
    target_country: str = "HKG"
    brand_terms: tuple[str, ...] = ()
    service_intent_terms: tuple[str, ...] = ()
    informational_terms: tuple[str, ...] = ()
    service_url_patterns: tuple[str, ...] = ()
    article_url_patterns: tuple[str, ...] = ()


@dataclass(frozen=True)
class AgentConfig:
    max_tool_calls: int = 8
    max_output_lines: int = 180
    timeout_seconds: int = 90
    provider: str = "none"
    allow_remote: bool = False
    ollama_host: str = "http://127.0.0.1:11434"
    ollama_model: str = "llama3.1"
    openai_base_url: str = ""
    openai_model: str = ""


@dataclass(frozen=True)
class AppConfig:
    oauth_client_secrets_file: str = ""
    token_file: str = ""
    database_path: str = ""
    analysis: AnalysisConfig = field(default_factory=AnalysisConfig)
    scoring: ScoringConfig = field(default_factory=ScoringConfig)
    agent: AgentConfig = field(default_factory=AgentConfig)
    source_path: str = ""


def _strings(data: dict, key: str) -> tuple[str, ...]:
    value = data.get(key, [])
    if value is None:
        return ()
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ConfigError(f"{key} 必須是字串陣列")
    return tuple(item.strip() for item in value if item.strip())


def _bands(scoring: dict) -> tuple[PositionBand, ...]:
    raw_bands = scoring.get("position_bands", [])
    if not isinstance(raw_bands, list):
        raise ConfigError("scoring.position_bands 必須是陣列")
    bands: list[PositionBand] = []
    for item in raw_bands:
        if not isinstance(item, dict):
            raise ConfigError("每個 position band 必須是表")
        try:
            bands.append(
                PositionBand(
                    min_position=float(item["min_position"]),
                    max_position=float(item["max_position"]),
                    max_clicks=float(item["max_clicks"]),
                    reason=str(item.get("reason") or "").strip(),
                )
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ConfigError("position band 缺少 min_position、max_position 或 max_clicks") from exc
    return tuple(bands)


def config_from_mapping(data: dict, *, source_path: str = "") -> AppConfig:
    oauth = data.get("oauth") or {}
    storage = data.get("storage") or {}
    analysis = data.get("analysis") or {}
    scoring = data.get("scoring") or {}
    agent = data.get("agent") or {}
    if not all(isinstance(section, dict) for section in (oauth, storage, analysis, scoring, agent)):
        raise ConfigError("設定檔的區段必須是表")
    scoring_config = ScoringConfig(
        min_impressions_for_ranking=int(scoring.get("min_impressions_for_ranking", 50)),
        low_confidence_impressions=int(scoring.get("low_confidence_impressions", 30)),
        min_article_clicks_for_link=int(scoring.get("min_article_clicks_for_link", 3)),
        position_bands=_bands(scoring),
    )
    return AppConfig(
        oauth_client_secrets_file=str(oauth.get("client_secrets_file") or ""),
        token_file=str(oauth.get("token_file") or ""),
        database_path=str(storage.get("database_path") or ""),
        analysis=AnalysisConfig(
            target_country=str(analysis.get("target_country") or "HKG"),
            brand_terms=_strings(analysis, "brand_terms"),
            service_intent_terms=_strings(analysis, "service_intent_terms"),
            informational_terms=_strings(analysis, "informational_terms"),
            service_url_patterns=_strings(analysis, "service_url_patterns"),
            article_url_patterns=_strings(analysis, "article_url_patterns"),
        ),
        scoring=scoring_config,
        agent=AgentConfig(
            max_tool_calls=int(agent.get("max_tool_calls", 8)),
            max_output_lines=int(agent.get("max_output_lines", 180)),
            timeout_seconds=int(agent.get("timeout_seconds", 90)),
            provider=str(agent.get("provider") or "none"),
            allow_remote=bool(agent.get("allow_remote", False)),
            ollama_host=str(agent.get("ollama_host") or "http://127.0.0.1:11434"),
            ollama_model=str(agent.get("ollama_model") or "llama3.1"),
            openai_base_url=str(agent.get("openai_base_url") or ""),
            openai_model=str(agent.get("openai_model") or ""),
        ),
        source_path=source_path,
    )


def load_config(path: Path) -> AppConfig:
    if not path.is_file():
        raise ConfigError(f"找不到設定檔：{path}")
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"設定檔不是有效的 TOML：{path.name}") from exc
    if not isinstance(data, dict):
        raise ConfigError("設定檔的根節點必須是表")
    return config_from_mapping(data, source_path=str(path))


def resolve_config_path(explicit: Path | None = None) -> Path | None:
    if explicit is not None:
        return explicit
    env_value = os.environ.get("GSC_AGENT_CONFIG")
    if env_value:
        return Path(env_value)
    local = Path("config.toml")
    if local.is_file():
        return local
    return None


def resolve_runtime_paths(config: AppConfig, *, database_override: Path | None = None) -> RuntimePaths:
    if database_override is not None:
        database = database_override
    elif config.database_path:
        database = Path(config.database_path).expanduser()
    else:
        database = default_database_path()
    secrets = (
        Path(config.oauth_client_secrets_file).expanduser()
        if config.oauth_client_secrets_file
        else default_client_secrets_path()
    )
    token = Path(config.token_file).expanduser() if config.token_file else default_token_path()
    return RuntimePaths(database=database, token=token, client_secrets=secrets)
