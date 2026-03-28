from __future__ import annotations

import re

from rtms.shared.enums import Role
from rtms.shared.schemas import (
    HighAltitudeCCBuildConfig,
    HighAltitudeCCBuildConfigConstraints,
)


HIGH_ALTITUDE_CC_REPO_ID = "high-altitude-cc"
HIGH_ALTITUDE_CC_APP_CONFIG_PATH = "Core/Inc/app_config.h"
HIGH_ALTITUDE_CC_ROLE_MACROS = {
    Role.TX: "APP_ROLE_MODE_TX",
    Role.RX: "APP_ROLE_MODE_RX",
}
_GUARDED_DEFINE_RE = re.compile(r"^\s*#\s*ifndef\s+(?P<macro>[A-Za-z_][A-Za-z0-9_]*)\s*$")
_NESTED_IF_RE = re.compile(r"^\s*#\s*(?P<directive>ifdef|ifndef)\s+(?P<macro>[A-Za-z_][A-Za-z0-9_]*)\s*$")
_ANY_IF_RE = re.compile(r"^\s*#\s*(if|ifdef|ifndef)\b")
_ELSE_RE = re.compile(r"^\s*#\s*else\b")
_ENDIF_RE = re.compile(r"^\s*#\s*endif\b")
_DEFINE_RE = re.compile(r"^\s*#\s*define\s+(?P<macro>[A-Za-z_][A-Za-z0-9_]*)\s+(?P<value>.+?)\s*$")
_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _strip_macro_value(value: str) -> str:
    value = re.sub(r"/\*.*?\*/", "", value)
    value = re.sub(r"//.*$", "", value)
    value = value.strip()
    if value.startswith("(") and value.endswith(")"):
        value = value[1:-1].strip()
    return value


def _find_guarded_block(lines: list[str], macro: str) -> tuple[int, int]:
    start = None
    for index, line in enumerate(lines):
        match = _GUARDED_DEFINE_RE.match(line)
        if match and match.group("macro") == macro:
            start = index
            break
    if start is None:
        raise ValueError(f"missing default macro definition for {macro}")

    depth = 0
    for index in range(start, len(lines)):
        line = lines[index]
        if _ANY_IF_RE.match(line):
            depth += 1
            continue
        if _ENDIF_RE.match(line):
            depth -= 1
            if depth == 0:
                return start, index
    raise ValueError(f"unterminated default macro definition for {macro}")


def _extract_effective_macro_value(source: str, macro: str) -> str:
    lines = source.splitlines()
    start, end = _find_guarded_block(lines, macro)
    active_stack = [True]
    branch_stack: list[tuple[bool, bool]] = []

    for line in lines[start + 1 : end]:
        match = _NESTED_IF_RE.match(line)
        if match:
            parent_active = active_stack[-1]
            condition = False if match.group("directive") == "ifdef" else True
            branch_stack.append((parent_active, condition))
            active_stack.append(parent_active and condition)
            continue
        if _ELSE_RE.match(line):
            if not branch_stack:
                raise ValueError(f"unexpected #else while parsing {macro}")
            parent_active, condition = branch_stack[-1]
            active_stack[-1] = parent_active and not condition
            continue
        if _ENDIF_RE.match(line):
            if not branch_stack:
                raise ValueError(f"unexpected #endif while parsing {macro}")
            branch_stack.pop()
            active_stack.pop()
            continue
        if not active_stack[-1]:
            continue
        match = _DEFINE_RE.match(line)
        if match and match.group("macro") == macro:
            return _strip_macro_value(match.group("value"))

    raise ValueError(f"missing active default value for {macro}")


def _parse_int(value: str) -> int:
    normalized = re.sub(r"(?<=\d)[uUlL]+$", "", value.strip())
    return int(normalized, 10)


def _resolve_macro_int(source: str, macro: str, *, seen: set[str] | None = None) -> int:
    path = seen or set()
    if macro in path:
        chain = " -> ".join([*sorted(path), macro])
        raise ValueError(f"cyclic macro resolution detected: {chain}")
    value = _extract_effective_macro_value(source, macro)
    try:
        return _parse_int(value)
    except ValueError:
        if not _IDENTIFIER_RE.match(value):
            raise ValueError(f"unsupported macro value for {macro}: {value}") from None
        return _resolve_macro_int(source, value, seen=path | {macro})


def high_altitude_cc_build_constraints() -> HighAltitudeCCBuildConfigConstraints:
    return HighAltitudeCCBuildConfigConstraints()


def parse_high_altitude_cc_build_config(source: str) -> HighAltitudeCCBuildConfig:
    return HighAltitudeCCBuildConfig(
        machine_log_detail=_resolve_macro_int(source, "APP_MACHINE_LOG_DETAIL"),
        machine_log_stat_period_ms=_resolve_macro_int(source, "APP_MACHINE_LOG_STAT_PERIOD_MS"),
    )


def build_high_altitude_cc_cdefs(role: Role, build_config: HighAltitudeCCBuildConfig) -> list[str]:
    return [
        f"-DAPP_ROLE_MODE={HIGH_ALTITUDE_CC_ROLE_MACROS[role]}",
        "-DAPP_HUMAN_LOG_ENABLE=0",
        "-DAPP_MACHINE_LOG_ENABLE=1",
        f"-DAPP_MACHINE_LOG_DETAIL={build_config.machine_log_detail}",
        f"-DAPP_MACHINE_LOG_STAT_PERIOD_MS={build_config.machine_log_stat_period_ms}U",
    ]
