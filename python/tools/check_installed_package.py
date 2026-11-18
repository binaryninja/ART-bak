"""Check an installed wheel's files and inert public commands, without starting ARTEX.

Run with the installation's interpreter, outside the source checkout:
    PYTHONPATH= /path/to/venv/bin/python -I -B /path/to/checkout/python/tools/check_installed_package.py

The package must be a regular wheel installation, not an editable/source import.
Only package bytes, --help and synthetic --check-config inputs are inspected.
No schema is executed; no task, database, provider, listener or runtime is built.
Success proves these installation checks only, not operational task readiness.
The script uses the standard library and does not install optional dependencies.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
from importlib import metadata, resources
import json
from pathlib import Path
import subprocess
import sys
import tempfile


PACKAGE_FILES = (
    "artex/db/schema.sql",
    "artex/db/timed_source_manifest.json",
    "artex/traffic/schema.sql",
    # These prompt bodies/renderers are Python modules, not external templates.
    "artex/agent/prompts.py",
    "artex/agent/planner_prompts.py",
    "artex/agent/additional_prompts.py",
    "artex/agent/prompt_render.py",
)


def inspect_files():
    import artex

    distribution = metadata.distribution("artex-python")
    entries = {str(item): item for item in distribution.files or ()}
    init = entries.get("artex/__init__.py")
    if init is None or Path(artex.__file__).resolve() != Path(distribution.locate_file(init)).resolve():
        raise ValueError("Expected a regular installed wheel; source/editable imports are not accepted")
    root = resources.files("artex")
    checks = []
    for name in PACKAGE_FILES:
        entry = entries.get(name)
        try:
            data = root.joinpath(*name.split("/")[1:]).read_bytes()
        except FileNotFoundError:
            checks.append({"file": name, "ok": False, "reason": "missing installed file"})
            continue
        digest = hashlib.sha256(data).digest()
        encoded = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
        valid = (entry is not None and entry.hash is not None
                 and entry.hash.mode == "sha256" and entry.hash.value == encoded
                 and entry.size == len(data) and len(data) > 0)
        checks.append({"file": name, "ok": valid, "bytes": len(data),
                       "sha256": digest.hex(), "record_matches": valid})
    return {"distribution": distribution.metadata["Name"], "version": distribution.version,
            "package_file": artex.__file__, "files": checks}


def inspect_commands():
    # Deliberately do not inherit application credentials, config or PYTHONPATH.
    # The URI is a synthetic string; --check-config must never connect to it.
    environment = {"PYTHONPATH": "", "LANG": "C.UTF-8"}
    configured = {**environment, "ARTEX_PG_DSN": "postgresql://synthetic.invalid/unused",
                  "ARTEX_SIGNING_KEY_HEX": "ab" * 32}
    checks = []
    with tempfile.TemporaryDirectory(prefix="artex-installed-check-") as temporary:
        directory = Path(temporary)
        valid = directory / "valid-tuning.json"
        invalid = directory / "invalid-tuning.json"
        valid.write_text(json.dumps({
            "version": 1,
            "listener": {"host": "127.0.0.1", "port": 8787,
                         "max_connections": 8, "socket_timeout": 10},
            "pump": {"interval": 1, "max_per_tick": 8},
            "reads": {"max_rows": 500, "max_bytes": 1048576},
        }), encoding="utf-8")
        invalid.write_text("{}", encoding="utf-8")
        cases = [
            ("artex", ["--help"], environment, 0, "--check-config"),
            ("artex.runtime_config_check", ["--help"], environment, 0, "--check-config"),
            ("artex.skills_workspace", ["--help"], environment, 0, "--root"),
            ("artex", ["--check-config"], configured, 0, "Configuration valid."),
            ("artex", ["--check-config"], environment, 2, "ARTEX_PG_DSN is required"),
            ("artex.runtime_config_check", ["--check-config", str(valid)], environment,
             0, "Runtime tuning syntax valid."),
            ("artex.runtime_config_check", ["--check-config", str(invalid)], environment,
             2, "Runtime tuning check failed."),
        ]
        for module, arguments, env, expected_code, expected_text in cases:
            command = [sys.executable, "-I", "-B", "-m", module, *arguments]
            result = subprocess.run(command, cwd=directory, env=env,
                                    capture_output=True, text=True, timeout=10)
            checks.append({"module": module, "arguments": arguments,
                           "exit_code": result.returncode, "expected_exit_code": expected_code,
                           "ok": result.returncode == expected_code
                           and expected_text in result.stdout + result.stderr})
    return checks


def main(argv=None):
    argparse.ArgumentParser(description=__doc__,
                            formatter_class=argparse.RawDescriptionHelpFormatter).parse_args(argv)
    try:
        report = inspect_files()
        report["commands"] = inspect_commands()
        report["python"] = sys.version.split()[0]
        report["isolated_interpreter"] = bool(sys.flags.isolated)
        report["ok"] = all(item["ok"] for item in report["files"] + report["commands"])
        report["scope"] = "Installed files and inert commands only; no runtime readiness claim"
    except (ImportError, metadata.PackageNotFoundError, OSError, ValueError, subprocess.SubprocessError):
        print("Installed-package verification could not finish; use a regular wheel installation and supported Python.",
              file=sys.stderr)
        return 2
    print(json.dumps(report, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
