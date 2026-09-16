#!/usr/bin/env python3
"""Deploy Sea of Colours Snowflake artefacts.

Default behaviour (NON-DESTRUCTIVE):
  1. snowflake/soc_schema.sql       — CREATE TABLE IF NOT EXISTS
  2. sea_of_colours/orchestrator_2/snowflake/orchestrator_v2_schema.sql
                                    — SOC_AGENT_BINDING + SOC_AGENT_MEMORY
  3. snowflake/soc_views.sql        — CREATE OR REPLACE VIEW
  4. snowflake/soc_procedures.sql   — Snowpark Python stored procs (Phase 2)

Tables are NEVER dropped here. Phase 2 files are deployed only when they
exist on disk, so this script is safe to run after Phase 1 alone.

There are no Cortex *agent objects* to deploy: the `soc_create_agent*.sql`
specs were removed with the Agents-API runtime. V12 reaches Cortex
inference over REST with a PAT, so nothing about the LLM agent needs a
Snowflake deploy step — see docs/SNOWFLAKE_SETUP.md.

Flags:
  --schema-only   Stop after soc_schema.sql + soc_views.sql.
  --no-procs      Skip soc_procedures.sql.
  --config FILE   Force a legacy key=value config file. Omit it and the
                  standard connection store is used (v1.45) — the same
                  ~/.snowflake/connections.toml the Snowflake CLI reads;
                  SOC_SNOWFLAKE_CONNECTION picks the section.

Target objects (database / schema / warehouse) come from
``sea_of_colours/snowpark/naming.py`` — override with ``SOC_DATABASE``,
``SOC_SCHEMA``, ``SOC_WAREHOUSE``. The defaults are hackathon-scoped
(``SOC_HACKATHON_DB`` / ``SOC_HACKATHON_WH``) so deploying into an
account that already runs SOC cannot clobber the existing install. Both
the database and the warehouse are created if absent, so this works on a
brand-new trial account.

Reads credentials from a Snowflake config file with these keys (matches
AA4 convention):

  account=<account>
  user=<user>
  private_key_file=<path to PEM>
  warehouse=<warehouse>            # optional, overrides SOC_WAREHOUSE
  database=<database>              # optional, overrides SOC_DATABASE
  schema=<schema>                  # optional, overrides SOC_SCHEMA
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import List

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sea_of_colours.snowpark import naming


DEFAULT_ROLE = os.environ.get("SF_ROLE", "ACCOUNTADMIN")

HERE = Path(__file__).resolve().parent.parent
SNOWFLAKE_DIR = HERE / "snowflake"
# orchestrator_2 keeps its tables next to the code that owns them. They are
# still part of the one deploy an attendee runs — nothing else creates them.
ORCHESTRATOR_SQL_DIR = HERE / "sea_of_colours" / "orchestrator_2" / "snowflake"


def _load_sf_props(path: str | None = None) -> dict:
    """Credentials, standard ``connections.toml`` first (v1.45).

    Kept as a thin shim because half a dozen modules import this name;
    the resolution itself now lives in :mod:`sea_of_colours.snowpark.sfconn`
    so the game reads the same connection store as the Snowflake CLI.
    """
    from sea_of_colours.snowpark import sfconn

    return sfconn.load_props(path)


def create_snowpark_session(config_file: str | None = None, *, with_context: bool = True):
    """Build a Snowpark session from the resolved Snowflake connection.

    ``with_context=False`` connects without pinning database / schema.
    The deploy path needs that: on a fresh account the database does not
    exist yet, and Snowflake refuses the connection if you name a
    missing database up front.

    v1.45 — ``config_file`` is now optional and only forces a specific
    legacy file. Pass nothing and the standard connection store wins.
    """
    from snowflake.snowpark import Session
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.backends import default_backend
    from sea_of_colours.snowpark import sfconn

    props = _load_sf_props(config_file)
    if not props:
        _, where = sfconn.resolve_source()
        raise FileNotFoundError(f"No Snowflake connection: {where}")
    if "private_key_file" not in props:
        _, where = sfconn.resolve_source()
        raise KeyError(
            f"{where} has no private_key_file — key-pair auth is what the "
            "persistence path needs; see docs/SNOWFLAKE_SETUP.md §2"
        )

    key_path = props["private_key_file"]
    if not os.path.exists(key_path):
        raise FileNotFoundError(f"Private key file not found: {key_path}")

    with open(key_path, "rb") as kf:
        private_key = serialization.load_pem_private_key(
            kf.read(), password=None, backend=default_backend(),
        )
    private_key_bytes = private_key.private_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )

    cfg = {
        "account": props.get("account"),
        "user": props.get("user"),
        "private_key": private_key_bytes,
        "warehouse": props.get("warehouse", naming.warehouse()),
    }
    # v1.45 — a standard connection usually names a role, and omitting it
    # silently lands you on the user's default, which is the sort of
    # difference that only shows up as a permissions error much later.
    if props.get("role"):
        cfg["role"] = props["role"]
    if with_context:
        cfg["database"] = props.get("database", naming.database())
        cfg["schema"] = props.get("schema", naming.schema())
    return Session.builder.configs(cfg).create()


def build_engine_zip() -> Path:
    """Zip the ``sea_of_colours`` package for IMPORTS into Snowpark procs.

    Snowflake's IMPORTS clause expects a stage path to a zip / .py file
    whose top-level entries match the import names. We package the entire
    ``sea_of_colours`` package (engine + snowpark layer) under one zip so
    every proc handler can resolve ``import sea_of_colours.snowpark.procs``.
    """
    import zipfile

    out_dir = HERE / "build"
    out_dir.mkdir(exist_ok=True)
    zip_path = out_dir / "sea_of_colours.zip"
    pkg_root = HERE / "sea_of_colours"
    if not pkg_root.is_dir():
        raise FileNotFoundError(f"package not found: {pkg_root}")

    print()
    print("=" * 60)
    print(f"Packaging {pkg_root.name} -> {zip_path}")
    print("=" * 60)

    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in pkg_root.rglob("*"):
            if path.is_dir():
                continue
            if any(seg.startswith("__pycache__") for seg in path.parts):
                continue
            if path.suffix in {".pyc", ".pyo"}:
                continue
            arcname = str(path.relative_to(HERE))
            zf.write(path, arcname)
    print(f"  packaged {zip_path.stat().st_size // 1024} KB")
    return zip_path


def upload_engine_zip(session, zip_path: Path) -> None:
    """PUT the engine zip onto @SOC_PY_STAGE (idempotent)."""
    print()
    print("=" * 60)
    print(f"Uploading {zip_path.name} to @SOC_PY_STAGE...")
    print("=" * 60)
    session.sql(
        "CREATE STAGE IF NOT EXISTS SOC_PY_STAGE "
        "DIRECTORY = (ENABLE = TRUE) "
        "COMMENT = 'Python imports for SOC_* Snowpark procedures.'"
    ).collect()
    abs_path = zip_path.resolve()
    put_sql = (
        f"PUT 'file://{abs_path}' @SOC_PY_STAGE "
        "AUTO_COMPRESS=FALSE OVERWRITE=TRUE"
    )
    print(f"  {put_sql}")
    result = session.sql(put_sql).collect()
    if result:
        first = result[0].as_dict() if hasattr(result[0], "as_dict") else dict(result[0])
        print(f"  uploaded: {first}")


def _split_statements(sql_text: str) -> List[str]:
    """Split a SQL file into individual statements.

    Mirrors AA4's splitter so $$-delimited Python procedure / agent bodies
    survive in one statement. Lines outside a body that are blank or pure
    SQL comments are dropped; everything else accumulates until a top-level
    semicolon (``;``) or, inside a body, ``$$;``.
    """
    statements: List[str] = []
    current: List[str] = []
    in_body = False

    for raw_line in sql_text.split("\n"):
        stripped = raw_line.strip()

        if not in_body and (not stripped or stripped.startswith("--")):
            continue

        current.append(raw_line)
        upper = stripped.upper()

        if any(
            kw in upper
            for kw in (
                "CREATE OR REPLACE PROCEDURE",
                "CREATE PROCEDURE",
                "CREATE OR REPLACE AGENT",
                "CREATE AGENT",
            )
        ):
            in_body = True

        if stripped.endswith(";"):
            if in_body and "$$;" in raw_line:
                in_body = False
                statements.append("\n".join(current))
                current = []
            elif not in_body:
                statements.append("\n".join(current))
                current = []

    if current:
        statements.append("\n".join(current))

    return [s.strip() for s in statements if s.strip()]


def deploy_sql_file(session, filepath: Path, *, label: str | None = None) -> None:
    """Execute every statement in ``filepath`` against ``session``."""
    label = label or filepath.name
    print()
    print("=" * 60)
    print(f"Deploying: {label}  ({filepath})")
    print("=" * 60)

    if not filepath.exists():
        print(f"  (skipped — file does not exist)")
        return

    rendered = naming.render_sql(filepath.read_text())

    # Isolation guard. A file that still names a database literally would
    # quietly write outside the resolved target — which is precisely the
    # cross-deployment clobber this indirection exists to prevent. Fail
    # loudly and name the file instead.
    stray = naming.stray_literals(rendered)
    if stray:
        raise RuntimeError(
            f"{filepath.name} hard-codes {', '.join(sorted(stray))} instead "
            f"of the {{{{SOC_DATABASE}}}} / {{{{SOC_SCHEMA}}}} placeholders. "
            f"Deploying it would target a database other than "
            f"{naming.describe()}. Template the file (or delete it if it "
            f"belongs to a retired agent)."
        )

    statements = _split_statements(rendered)
    for i, stmt in enumerate(statements, 1):
        preview = stmt[:200] + ("..." if len(stmt) > 200 else "")
        print(f"\n[{i}/{len(statements)}] {preview}")
        try:
            session.sql(stmt).collect()
            print("  OK")
        except Exception as e:  # pragma: no cover - depends on live SF
            msg = str(e)
            if "already exists" in msg.lower():
                print(f"  OK (already exists)")
                continue
            print(f"  ERROR: {e}")
            raise


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument(
        "--config",
        default=None,
        help=(
            "Force a legacy key=value config file. Omit this and the "
            "standard Snowflake connection store is used "
            "(~/.snowflake/connections.toml; pick a section with "
            "SOC_SNOWFLAKE_CONNECTION)."
        ),
    )
    ap.add_argument(
        "--schema-only",
        action="store_true",
        help="Deploy only soc_schema.sql + soc_views.sql; skip procs / agent.",
    )
    ap.add_argument(
        "--no-procs",
        action="store_true",
        help="Skip soc_procedures.sql (default: include if file exists).",
    )
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help=(
            "Print the resolved target (database / schema / warehouse) "
            "and exit without connecting. Use this to confirm you are "
            "not about to deploy over another install."
        ),
    )
    args = ap.parse_args()

    try:
        db, sch, wh = naming.database(), naming.schema(), naming.warehouse()
    except ValueError as e:
        # Misconfigured target name — a stack trace here just buries the
        # one line that tells you which env var to fix.
        print(f"error: {e}", file=sys.stderr)
        print(
            "       set SOC_DATABASE / SOC_SCHEMA / SOC_WAREHOUSE to a "
            "plain Snowflake identifier (letters, digits, _ and $).",
            file=sys.stderr,
        )
        return 2

    print(f"Target: {naming.describe()}")
    if args.dry_run:
        print("(--dry-run: no connection opened, nothing deployed)")
        return 0

    print("Creating Snowpark session...")
    # No database context on connect — it may not exist yet.
    session = create_snowpark_session(args.config, with_context=False)

    # Pin role / warehouse / database / schema before anything else fires.
    role = DEFAULT_ROLE
    try:
        session.sql(f"USE ROLE {role}").collect()
    except Exception as e:  # pragma: no cover - depends on live SF
        print(f"  (could not USE ROLE {role}: {e}; continuing with session default)")

    # Bootstrap the container objects. Both are IF NOT EXISTS, so this is
    # a no-op against an established deployment and the whole path works
    # on a brand-new trial account that has neither.
    print(f"\nEnsuring warehouse {wh} ...")
    session.sql(
        f"CREATE WAREHOUSE IF NOT EXISTS {wh} "
        f"WAREHOUSE_SIZE = XSMALL "
        f"AUTO_SUSPEND = 60 "
        f"AUTO_RESUME = TRUE "
        f"INITIALLY_SUSPENDED = TRUE "
        f"COMMENT = 'Sea of Colours — XSMALL, idles down after 60s.'"
    ).collect()
    session.sql(f"USE WAREHOUSE {wh}").collect()

    print(f"Ensuring database {db} ...")
    session.sql(
        f"CREATE DATABASE IF NOT EXISTS {db} "
        f"COMMENT = 'Sea of Colours game state.'"
    ).collect()
    session.sql(f"USE DATABASE {db}").collect()
    session.sql(f"CREATE SCHEMA IF NOT EXISTS {sch}").collect()
    session.sql(f"USE SCHEMA {sch}").collect()

    deploy_sql_file(session, SNOWFLAKE_DIR / "soc_schema.sql", label="schema")
    # v1.19 — SOC_AGENT_MEMORY / SOC_AGENT_BINDING moved here when
    # orchestrator_2 landed, and this file was never added to the deploy, so
    # no account has ever had them. V12 mirrors its journal, hazard memory and
    # frontier memory to SOC_AGENT_MEMORY best-effort inside bare excepts, so
    # the absence was silent: the in-process cache carried a headless season
    # fine, and a live season lost the lot whenever the server restarted.
    deploy_sql_file(
        session,
        ORCHESTRATOR_SQL_DIR / "orchestrator_v2_schema.sql",
        label="orchestrator_2 schema",
    )
    deploy_sql_file(session, SNOWFLAKE_DIR / "soc_views.sql", label="views")

    if not args.schema_only:
        if not args.no_procs:
            zip_path = build_engine_zip()
            upload_engine_zip(session, zip_path)
            deploy_sql_file(
                session,
                SNOWFLAKE_DIR / "soc_procedures.sql",
                label="procedures",
            )

    print()
    print("=" * 60)
    print("Deployment complete.")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
