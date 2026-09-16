"""Single source of truth for the Snowflake objects SOC deploys into.

Every database / schema / warehouse name used by the deploy script, the
SQL files, the Snowpark store and the Cortex invoker resolves through
here. Nothing downstream should hard-code ``UMAN_SIM_DB`` (or any other
literal) again — that drift is exactly how a hackathon deploy ends up
overwriting somebody's existing dev deployment.

**The defaults are deliberately hackathon-specific.** A fresh attendee
account gets its own ``SOC_HACKATHON_DB`` / ``SOC_HACKATHON_WH``, which
cannot collide with a pre-existing SOC install in the same account. If
you are pointing at an older deployment, set the env vars explicitly.

Resolution order, highest first:

1. Environment — ``SOC_DATABASE`` / ``SOC_SCHEMA`` / ``SOC_WAREHOUSE``.
2. The ``database=`` / ``schema=`` / ``warehouse=`` keys in your
   ``sf_config`` file (applied by the caller that parses it).
3. The hackathon defaults below.
"""

from __future__ import annotations

import os


# Defaults. Named for the hackathon so a from-scratch deploy is isolated
# by construction rather than by the operator remembering to override.
DEFAULT_DATABASE = "SOC_HACKATHON_DB"
DEFAULT_SCHEMA = "SEA_OF_COLOURS"
DEFAULT_WAREHOUSE = "SOC_HACKATHON_WH"

# Snowflake identifiers we generate are unquoted, so they fold to upper
# case server-side. Normalise here so a lower-case env var doesn't cause
# a spurious mismatch when we compare a resolved name against a literal.
_IDENT_OK = set("ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_$")


def _ident(value: str, *, field: str) -> str:
    """Validate + normalise an unquoted Snowflake identifier.

    These names are interpolated straight into DDL (you cannot bind an
    identifier as a parameter), so refuse anything that isn't a plain
    identifier rather than passing it through to the server.
    """
    name = (value or "").strip().strip('"').upper()
    if not name:
        raise ValueError(f"{field} resolved to an empty identifier")
    if name[0].isdigit():
        raise ValueError(f"{field}={name!r} may not start with a digit")
    bad = sorted(set(name) - _IDENT_OK)
    if bad:
        raise ValueError(
            f"{field}={name!r} contains characters that are unsafe to "
            f"interpolate into DDL: {''.join(bad)!r}"
        )
    return name


def database() -> str:
    """Target database — ``SOC_DATABASE`` or the hackathon default."""
    return _ident(
        os.environ.get("SOC_DATABASE", DEFAULT_DATABASE),
        field="SOC_DATABASE",
    )


def schema() -> str:
    """Target schema — ``SOC_SCHEMA`` or the hackathon default."""
    return _ident(
        os.environ.get("SOC_SCHEMA", DEFAULT_SCHEMA),
        field="SOC_SCHEMA",
    )


def warehouse() -> str:
    """Target warehouse — ``SOC_WAREHOUSE`` or the hackathon default."""
    return _ident(
        os.environ.get("SOC_WAREHOUSE", DEFAULT_WAREHOUSE),
        field="SOC_WAREHOUSE",
    )


def qualified(table: str) -> str:
    """``DB.SCHEMA.TABLE`` for the resolved target."""
    return f"{database()}.{schema()}.{table}"


def render_sql(sql_text: str) -> str:
    """Substitute the ``{{SOC_*}}`` placeholders in a deploy SQL file.

    The checked-in ``.sql`` files carry placeholders rather than literals
    so there is exactly one place a name can be wrong. Anything the
    deploy script sends to Snowflake goes through here first.
    """
    return (
        sql_text
        .replace("{{SOC_DATABASE}}", database())
        .replace("{{SOC_SCHEMA}}", schema())
        .replace("{{SOC_WAREHOUSE}}", warehouse())
    )


# Database names that used to be hard-coded across the SQL files. Any
# occurrence surviving into rendered SQL means that file was missed when
# the placeholders went in, and would deploy outside the resolved target.
LEGACY_DATABASE_LITERALS = ("UMAN_SIM_DB",)


def stray_literals(rendered_sql: str) -> set[str]:
    """Legacy database literals still present after :func:`render_sql`.

    Empty set means the text is safe to send: every object reference is
    either unqualified (and so resolves against the session's ``USE``
    context) or names the configured target.
    """
    upper = rendered_sql.upper()
    found = {lit for lit in LEGACY_DATABASE_LITERALS if lit in upper}
    # A literal that happens to BE the configured target is fine — that's
    # someone deliberately pointing at the old deployment.
    return found - {database()}


def describe() -> str:
    """One-line summary for deploy logs and the boot banner."""
    return f"{database()}.{schema()} (warehouse {warehouse()})"
