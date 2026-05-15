"""Configuration management with Pydantic and YAML."""

from __future__ import annotations

import os
from pathlib import Path

import yaml
from pydantic import BaseModel, Field


class CTPConfig(BaseModel):
    userid: str = ""
    password: str = ""
    brokerid: str = "7090"
    md_address: str = "tcp://180.169.75.18:61213"
    td_address: str = "tcp://180.169.75.18:61205"
    appid: str = "client_fmz_1.0"
    auth_code: str = ""


class TushareConfig(BaseModel):
    token: str = ""


class WatchlistItem(BaseModel):
    prefix: str
    exchange: str
    name: str = ""


class StrategyConfig(BaseModel):
    preset: str = "balanced"
    entry_timeframe: str = "15m"
    min_align_score: int = 25
    stop_atr: float = 1.5
    target_atr: float = 3.0
    trail_atr: float = 1.0
    max_hold_bars: int = 40
    cooldown: int = 2


class AutoTradeConfig(BaseModel):
    # 默认开启 — 启动 finme_serve 即按策略实盘下单。
    enabled: bool = True
    max_position_per_variety: int = 1
    max_total_positions: int = 3
    volume_per_signal: int = 1
    # 下单后等待 CTP 终态的秒数 (filled/rejected/cancelled)。
    # 设为 0 表示发出后立即返回 submitted，不等待成交回报。
    order_wait_seconds: float = 3.0
    # 评分低于该阈值的信号不进入实盘交易 (基于 compositeScore / signalScore)。
    min_score: int = 30
    confirm_scans: int = 2
    # Which signal sources are allowed to trigger trades.
    # "chanlun" = structural BSP signals, "execution" = mechanical entry layer.
    # Default: only chanlun confirmed signals drive real trades.
    allowed_sources: list[str] = Field(
        default_factory=lambda: ["chanlun"]
    )


class ExecutionConfig(BaseModel):
    """Mechanical entry layer: hub breakout, bi extreme, hub pullback."""

    enabled: bool = True
    min_atr_bar_ratio: float = 0.3
    max_sl_atr: float = 2.0
    hub_tolerance_atr: float = 0.8
    target_atr: float = 3.0
    min_alignment_tfs: int = 2
    rules: list[str] = Field(
        default_factory=lambda: ["hub_breakout", "bi_extreme", "hub_pullback"]
    )


class ScanConfig(BaseModel):
    interval_minutes: int = 15
    recent_bars: int = 5
    require_finished: bool = True
    include_partial_types: bool = False


class APIConfig(BaseModel):
    host: str = "0.0.0.0"
    port: int = 8080


class CacheConfig(BaseModel):
    enabled: bool = True
    dir: str = "data_cache"


class AppConfig(BaseModel):
    ctp: CTPConfig = Field(default_factory=CTPConfig)
    tushare: TushareConfig = Field(default_factory=TushareConfig)
    watchlist: list[WatchlistItem] = Field(default_factory=lambda: [
        WatchlistItem(prefix="C", exchange="DCE", name="玉米"),
        WatchlistItem(prefix="CS", exchange="DCE", name="淀粉"),
        WatchlistItem(prefix="M", exchange="DCE", name="豆粕"),
    ])
    strategy: StrategyConfig = Field(default_factory=StrategyConfig)
    auto_trade: AutoTradeConfig = Field(default_factory=AutoTradeConfig)
    execution: ExecutionConfig = Field(default_factory=ExecutionConfig)
    scan: ScanConfig = Field(default_factory=ScanConfig)
    api: APIConfig = Field(default_factory=APIConfig)
    cache: CacheConfig = Field(default_factory=CacheConfig)


def resolve_config_path(path: str | Path | None = None) -> Path:
    """Return the resolved config path using the same search order as load_config."""
    if path is None:
        path = os.environ.get("FINME_CONFIG", "config.yaml")
    return Path(path)


def load_config(path: str | Path | None = None) -> AppConfig:
    """Load configuration from YAML file.

    Search order: explicit path -> FINME_CONFIG env var -> ./config.yaml -> defaults.
    """
    p = resolve_config_path(path)
    if p.exists():
        with open(p, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        return AppConfig(**data)

    return AppConfig()


def save_config(config: AppConfig, path: str | Path = "config.yaml") -> None:
    """Save configuration to YAML file (drops YAML comments)."""
    p = Path(path)
    data = config.model_dump()
    with open(p, "w", encoding="utf-8") as f:
        yaml.dump(data, f, allow_unicode=True, default_flow_style=False, sort_keys=False)


def patch_auto_trade_enabled(path: str | Path, enabled: bool) -> None:
    """Persist the ``auto_trade.enabled`` flag while preserving YAML comments.

    Uses ruamel.yaml's round-trip loader so the user's existing config.yaml
    (with comments, ordering, credentials, etc.) stays intact.
    Falls back to a regex-based in-place rewrite if ruamel.yaml is unavailable.
    """
    p = Path(path)
    if not p.exists():
        # Nothing to patch — write a minimal config with the flag set.
        minimal = AppConfig()
        minimal.auto_trade.enabled = bool(enabled)
        save_config(minimal, p)
        return

    try:
        from ruamel.yaml import YAML  # type: ignore
        yaml_rt = YAML()
        yaml_rt.preserve_quotes = True
        yaml_rt.indent(mapping=2, sequence=4, offset=2)
        with open(p, "r", encoding="utf-8") as f:
            data = yaml_rt.load(f) or {}
        auto = data.get("auto_trade")
        if auto is None:
            data["auto_trade"] = {"enabled": bool(enabled)}
        else:
            auto["enabled"] = bool(enabled)
        with open(p, "w", encoding="utf-8") as f:
            yaml_rt.dump(data, f)
        return
    except Exception:
        pass

    import re
    text = p.read_text(encoding="utf-8")
    pattern = re.compile(
        r"(auto_trade:\s*(?:\r?\n)(?:[ \t]+[^\n]*(?:\r?\n))*?[ \t]+enabled:\s*)(true|false|True|False)",
        re.MULTILINE,
    )
    new_val = "true" if enabled else "false"
    new_text, n = pattern.subn(lambda m: m.group(1) + new_val, text, count=1)
    if n == 0:
        new_text = text.rstrip() + f"\n\nauto_trade:\n  enabled: {new_val}\n"
    p.write_text(new_text, encoding="utf-8")
