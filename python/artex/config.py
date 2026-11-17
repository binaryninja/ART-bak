"""Runtime configuration port of config/config.go (no database connections).

Python source execution anchors artifacts to the working directory. Frozen
executables anchor them beside the executable, analogous to the Go binary.
"""
from dataclasses import dataclass, field, fields
import json
import os
from pathlib import Path
import sys
from urllib.parse import quote, urlencode, urlsplit, urlunsplit


@dataclass
class Database:
    dsn: str = ""
    host: str = ""
    port: int = 0
    user: str = ""
    password: str = ""
    dbname: str = ""
    sslmode: str = ""

    def build_dsn(self) -> str:
        host = self.host or "127.0.0.1"
        # Bracket bare IPv6 addresses for a valid connection URI.
        if ":" in host and not host.startswith("["):
            host = f"[{host}]"
        authority = f"{host}:{self.port or 5432}"
        if self.user:
            credentials = quote(self.user, safe="")
            if self.password:
                credentials += ":" + quote(self.password, safe="")
            authority = credentials + "@" + authority
        return urlunsplit(("postgres", authority,
                           "/" + quote(self.dbname, safe="/"),
                           urlencode({"sslmode": self.sslmode or "disable"}), ""))


@dataclass
class Config:
    database: Database = field(default_factory=Database)
    skill_dir: str = ""


class _JSONObject(list):
    """Keep object member order/duplicates distinct from JSON arrays."""


class _JSONInteger(str):
    """Defer conversion so an overflowing port does not discard other fields."""


# These tags are ASCII. Go's simple case folding also equates long s and Kelvin
# sign with s/k; full Unicode casefold would incorrectly accept e.g. paßword.
_FIELD_CASE = str.maketrans("ABCDEFGHIJKLMNOPQRSTUVWXYZſK",
                          "abcdefghijklmnopqrstuvwxyzsk")
_DATABASE_FIELDS = frozenset(f.name for f in fields(Database))


def _invalid_constant(value: str):
    raise ValueError(f"Invalid JSON constant: {value}")


def base_dir() -> Path:
    return Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(".")


def config_path() -> Path:
    override = os.environ.get("ARTEX_CONFIG", "").strip()
    if override:
        return Path(override)
    candidates = [Path.cwd() / "config.json", base_dir() / "config.json"]
    return next((p for p in candidates if p.exists()), candidates[0])


def load() -> Config:
    try:
        data = json.loads(config_path().read_text(encoding="utf-8"),
                          object_pairs_hook=_JSONObject,
                          parse_int=_JSONInteger,
                          parse_constant=_invalid_constant)
    except (OSError, ValueError, UnicodeError):
        return Config()
    result = Config()
    if not isinstance(data, _JSONObject):
        return result
    # Like encoding/json into Go structs, merge repeated objects and process
    # members in order; null or incompatible values leave earlier values intact.
    for key, value in data:
        key = key.translate(_FIELD_CASE)
        if key == "database" and isinstance(value, _JSONObject):
            for name, item in value:
                name = name.translate(_FIELD_CASE)
                if name == "port" and isinstance(item, _JSONInteger):
                    try:
                        port = int(item)
                    except ValueError:
                        continue
                    if -sys.maxsize - 1 <= port <= sys.maxsize:
                        result.database.port = port
                elif name in _DATABASE_FIELDS and name != "port" and type(item) is str:
                    setattr(result.database, name, item)
        elif key == "skill_dir" and type(value) is str:
            result.skill_dir = value
    return result


def skill_dir() -> Path:
    location = (os.environ.get("ARTEX_SKILL_DIR", "").strip()
                or load().skill_dir.strip())
    result = Path(location) if location else base_dir() / "skills"
    result.mkdir(mode=0o755, parents=True, exist_ok=True)
    return result


def postgres_dsn() -> tuple[str, str]:
    override = os.environ.get("ARTEX_PG_DSN", "").strip()
    if override:
        return override, "environment variable ARTEX_PG_DSN"
    db = load().database
    if db.dsn.strip():
        return db.dsn.strip(), f"configuration file {config_path()} (database.dsn)"
    if db.host or db.dbname or db.user:
        return db.build_dsn(), f"configuration file {config_path()} (database fields)"
    raise ValueError(f"No database configuration: ARTEX_PG_DSN is unset and "
                     f"{config_path()} does not supply database.dsn or host/user/dbname. "
                     "Create the configuration file or set the environment variable.")


def redact(dsn: str) -> str:
    """Mask a URI password; fail closed for keyword/value or unknown DSNs."""
    try:
        parsed = urlsplit(dsn)
        if parsed.scheme not in ("postgres", "postgresql") or not parsed.netloc:
            return "[non-URI DSN redacted]"
        # PostgreSQL also accepts credentials in query parameters. Redact the
        # entire query/fragment rather than maintaining a fallible secret-key
        # allowlist (encoded, repeated and extension parameters are possible).
        netloc = parsed.netloc
        if "@" in netloc:
            userinfo, host = netloc.rsplit("@", 1)
            if ":" in userinfo:
                user = userinfo.split(":", 1)[0]
                netloc = f"{user}:****@{host}"
        return urlunsplit(parsed._replace(netloc=netloc,
                         query="[redacted]" if parsed.query else "",
                         fragment="[redacted]" if parsed.fragment else ""))
    except ValueError:
        # Safer than returning an unparsable URI containing credentials.
        return "[invalid DSN redacted]"
