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
        data = json.loads(config_path().read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeError):
        return Config()
    if not isinstance(data, dict):
        return Config()
    raw = data.get("database", {})
    values = {}
    if isinstance(raw, dict):
        for f in fields(Database):
            value = raw.get(f.name)
            expected = int if f.name == "port" else str
            if type(value) is expected:
                values[f.name] = value
    return Config(Database(**values), data.get("skill_dir", "")
                  if isinstance(data.get("skill_dir", ""), str) else "")


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
