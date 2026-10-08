"""User settings: pydantic models saved as ``settings.toml`` (docs/06 Stack: Config).

* ``debug`` flag and the live overlay refresh interval, hotkeys, the last window title (a rebinding hint; never a raw handle, #6) and
  per-feature params (validated by each feature's ``Params`` model when used, #15).
* One-time import of v1.8.0's ``NgoaLong_data.ini`` ``[PRO5]`` section (docs/04) when no
  ``settings.toml`` exists yet. The useful raw values are kept in ``legacy_ini`` so features added
  in later phases can import their own keys the first time their params are read.
"""

from __future__ import annotations

import configparser
import logging
import os
import re
import tomllib
from collections.abc import Iterable
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import tomli_w
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from ngoalong.engine.task import Task, TaskParams
from ngoalong.hotkeys import DEFAULT_DEBUG_TOGGLE, DEFAULT_STOP, normalize_hotkey
from ngoalong.paths import APP_ROOT

log = logging.getLogger(__name__)

PROJECT_ROOT = APP_ROOT  # the exe folder in the PyInstaller build (ngoalong/paths.py)
DEFAULT_SETTINGS_PATH = PROJECT_ROOT / "settings.toml"
DEFAULT_INI_PATH = PROJECT_ROOT / "NgoaLong_data.ini"
INI_SECTION = "PRO5"

# Live debug overlay refresh (``Settings.overlay_refresh_ms``): each tick captures and matches the
# whole canvas, so the interval is clamped to keep debug mode from hogging the CPU.
OVERLAY_REFRESH_DEFAULT_MS = 750
OVERLAY_REFRESH_MIN_MS = 250
OVERLAY_REFRESH_MAX_MS = 10_000

# [PRO5] keys never imported: raw handle (#6), the bot's own title (#5), leftovers (docs/04).
IGNORED_INI_KEYS = frozenset(
    {"HWND_GAME", "TITLE_ACTIVE", "ACP_LIST_TOADO_DEM", "WEBSITE_SELECT", "SERVER_SELECT",
     "SHOW_BEGIN_POPUP", "TRINHDUYET"}
)


class HotkeySettings(BaseModel):
    model_config = ConfigDict(extra="ignore", validate_assignment=True)

    stop: str = DEFAULT_STOP
    debug_toggle: str = DEFAULT_DEBUG_TOGGLE
    pause: str = ""  # empty = no hotkey

    @field_validator("stop", "debug_toggle", "pause")
    @classmethod
    def _valid(cls, v: str) -> str:
        return normalize_hotkey(v)


class WindowSettings(BaseModel):
    model_config = ConfigDict(extra="ignore")

    title: str = ""
    exe_path: str = ""


class UiSettings(BaseModel):
    model_config = ConfigDict(extra="ignore")

    last_feature: str = ""


class MultiWindowSettings(BaseModel):
    """"Nhiều cửa sổ" tab: which feature each game window runs.

    Keyed by the window title (it names the server/account and survives restarts); never by a raw
    handle (#6). Feature params are shared with the "Tính năng" tab (``Settings.features``).
    """

    model_config = ConfigDict(extra="ignore", validate_assignment=True)

    assignments: dict[str, str] = Field(default_factory=dict)  # window title -> feature id


_TELEGRAM_TOKEN_RE = re.compile(r"^\d{3,}:[A-Za-z0-9_-]{20,}$")
_TELEGRAM_CHAT_RE = re.compile(r"^(-?\d{1,20}|@[A-Za-z][A-Za-z0-9_]{3,})$")
_DISCORD_HOSTS = frozenset({"discord.com", "discordapp.com", "ptb.discord.com", "canary.discord.com"})


class TelegramSettings(BaseModel):
    model_config = ConfigDict(extra="ignore", validate_assignment=True)

    enabled: bool = False
    bot_token: str = ""
    chat_id: str = ""

    @field_validator("bot_token")
    @classmethod
    def _token(cls, v: str) -> str:
        v = v.strip()
        if v and not _TELEGRAM_TOKEN_RE.match(v):
            raise ValueError("mã bot Telegram không đúng dạng (123456:ABC...)")
        return v

    @field_validator("chat_id")
    @classmethod
    def _chat(cls, v: str) -> str:
        v = v.strip()
        if v and not _TELEGRAM_CHAT_RE.match(v):
            raise ValueError("chat id Telegram phải là số hoặc @tên_kênh")
        return v

    @property
    def ready(self) -> bool:
        return self.enabled and bool(self.bot_token) and bool(self.chat_id)


class DiscordSettings(BaseModel):
    model_config = ConfigDict(extra="ignore", validate_assignment=True)

    enabled: bool = False
    webhook_url: str = ""

    @field_validator("webhook_url")
    @classmethod
    def _url(cls, v: str) -> str:
        v = v.strip()
        if not v:
            return v
        u = urlsplit(v)
        if u.scheme != "https" or (u.hostname or "").lower() not in _DISCORD_HOSTS \
                or not u.path.startswith("/api/webhooks/"):
            raise ValueError("địa chỉ webhook Discord phải dạng https://discord.com/api/webhooks/...")
        return v

    @property
    def ready(self) -> bool:
        return self.enabled and bool(self.webhook_url)


class NotifySettings(BaseModel):
    """Notifications on finish / stop / error (docs/06 P3): tray, optional Telegram and Discord."""

    model_config = ConfigDict(extra="ignore", validate_assignment=True)

    tray: bool = True
    on_done: bool = True
    on_stopped: bool = True
    on_failed: bool = True
    screenshot: bool = True  # attach a screenshot to Telegram / Discord messages
    timeout_s: float = Field(15.0, ge=2.0, le=120.0)
    telegram: TelegramSettings = Field(default_factory=TelegramSettings)
    discord: DiscordSettings = Field(default_factory=DiscordSettings)


class GameSettings(BaseModel):
    """How the bot answers the game's own questions (pop-ups with a cost; ``popups.yaml`` policy)."""

    # "Bỏ qua thời gian chờ tốn N Vàng. Xác nhận?": True = 确定 (spend Vàng, no waiting),
    # False = 取消 and stop the task. Off by default: never spend Vàng unless asked to.
    spend_gold_skip_wait: bool = False
    # Collapse the HUD panels that cover the game (quest tracker, "Sắp mở", chat; not the top icons)
    # before every run (profiles/<profile>/panels.yaml).
    collapse_panels: bool = True


class Settings(BaseModel):
    model_config = ConfigDict(extra="ignore", validate_assignment=True)

    version: int = 1
    debug: bool = False
    draw_overlay: bool = False
    overlay_refresh_ms: int = OVERLAY_REFRESH_DEFAULT_MS  # debug overlay re-capture interval
    log_dir: str = "logs"
    profile: str = "ngoalongviet"
    hotkeys: HotkeySettings = Field(default_factory=HotkeySettings)
    window: WindowSettings = Field(default_factory=WindowSettings)
    ui: UiSettings = Field(default_factory=UiSettings)
    multi_window: MultiWindowSettings = Field(default_factory=MultiWindowSettings)
    notify: NotifySettings = Field(default_factory=NotifySettings)
    game: GameSettings = Field(default_factory=GameSettings)
    features: dict[str, dict[str, Any]] = Field(default_factory=dict)
    legacy_ini: dict[str, str] = Field(default_factory=dict)
    legacy_applied: list[str] = Field(default_factory=list)
    ini_imported_from: str = ""

    @model_validator(mode="before")
    @classmethod
    def _default_overlay_from_debug(cls, data: Any) -> Any:
        if isinstance(data, dict) and data.get("debug") and "draw_overlay" not in data:
            data["draw_overlay"] = True
        return data

    @field_validator("overlay_refresh_ms")
    @classmethod
    def _clamp_refresh(cls, v: int) -> int:
        """Out-of-range values (hand-edited file) are clamped, not rejected."""
        return min(max(v, OVERLAY_REFRESH_MIN_MS), OVERLAY_REFRESH_MAX_MS)

    @field_validator("multi_window", "notify", mode="wrap")
    @classmethod
    def _section_or_default(cls, v: Any, handler: Any, info: Any) -> Any:
        """A hand-edited, broken optional section falls back to its defaults; the rest is kept."""
        try:
            return handler(v)
        except ValidationError as exc:
            log.warning("settings section [%s] is invalid, using defaults: %s", info.field_name, exc)
            return handler({})

    # --- per-feature params ---------------------------------------------------------------------
    def feature_params(self, cls: type[Task]) -> TaskParams:
        """Saved params of a feature (legacy INI values on first use), validated; defaults if broken."""
        raw = self.features.get(cls.id)
        if raw is None and self.legacy_ini and cls.id not in self.legacy_applied:
            raw = self._apply_legacy(cls)
        try:
            return cls.Params.model_validate(raw or {})
        except ValidationError as exc:
            log.warning("saved params of %s are invalid, using defaults: %s", cls.id, exc)
            return cls.Params()

    def set_feature_params(self, fid: str, params: BaseModel) -> None:
        self.features = {**self.features, fid: params.model_dump(mode="json", exclude_none=True)}

    def _apply_legacy(self, cls: type[Task]) -> dict[str, Any] | None:
        try:
            values = cls.import_legacy(self.legacy_ini)
        except Exception:
            log.exception("legacy import of %s failed", cls.id)
            values = {}
        self.legacy_applied = [*self.legacy_applied, cls.id]
        if not values:
            return None
        try:
            params = cls.Params.model_validate(values)
        except ValidationError as exc:
            log.warning("legacy values of %s rejected: %s", cls.id, exc)
            return None
        self.set_feature_params(cls.id, params)
        return self.features[cls.id]

    def import_legacy(self, ini: dict[str, str], source: str, features: Iterable[type[Task]] = ()) -> None:
        self.legacy_ini = {k: v for k, v in ini.items() if k not in IGNORED_INI_KEYS}
        self.ini_imported_from = source
        for cls in features:
            if cls.id not in self.features and cls.id not in self.legacy_applied:
                self._apply_legacy(cls)


# --- files --------------------------------------------------------------------------------------
def read_ini_section(path: str | Path, section: str = INI_SECTION) -> dict[str, str]:
    """Raw key/values of one INI section (keys keep their case). UTF-8, else Windows-1258."""
    p = Path(path)
    data = p.read_bytes()
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = data.decode("cp1258", errors="replace")
    parser = configparser.ConfigParser(interpolation=None, strict=False)
    parser.optionxform = str  # type: ignore[assignment,method-assign]
    parser.read_string(text, source=str(p))
    if not parser.has_section(section):
        return {}
    return {k: v.strip() for k, v in parser.items(section)}


def load_settings(path: str | Path = DEFAULT_SETTINGS_PATH) -> Settings:
    """Read ``settings.toml``; defaults when missing. A broken file is kept as ``.bak``."""
    p = Path(path)
    if not p.is_file():
        return Settings()
    try:
        data = tomllib.loads(p.read_text(encoding="utf-8"))
        return Settings.model_validate(data)
    except (tomllib.TOMLDecodeError, ValidationError, UnicodeDecodeError) as exc:
        backup = p.with_name(p.name + ".bak")
        log.error("settings file %s is invalid (%s); kept as %s, using defaults", p, exc, backup)
        try:
            os.replace(p, backup)
        except OSError:
            pass
        return Settings()


def save_settings(settings: Settings, path: str | Path = DEFAULT_SETTINGS_PATH) -> Path:
    """Write atomically (temporary file, then rename)."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    data = settings.model_dump(mode="json", exclude_none=True)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(tomli_w.dumps(data), encoding="utf-8")
    os.replace(tmp, p)
    return p


def load_or_import(
    path: str | Path = DEFAULT_SETTINGS_PATH,
    ini_path: str | Path | None = DEFAULT_INI_PATH,
    features: Iterable[type[Task]] = (),
) -> tuple[Settings, bool]:
    """Settings from ``path``; on first start, import the v1.8.0 INI once and save.

    Returns ``(settings, imported)``.
    """
    p = Path(path)
    if p.is_file():
        return load_settings(p), False
    settings = Settings()
    if ini_path is None or not Path(ini_path).is_file():
        return settings, False
    try:
        ini = read_ini_section(ini_path)
    except (OSError, configparser.Error) as exc:
        log.warning("cannot read %s: %s", ini_path, exc)
        return settings, False
    settings.import_legacy(ini, str(ini_path), features)
    save_settings(settings, p)
    return settings, True
