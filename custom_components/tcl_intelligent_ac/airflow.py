"""Airflow codes and capability bits from the TCL 7c500000 app profile."""

from __future__ import annotations

from typing import Any

VERTICAL = "tcl_vdir"
HORIZONTAL = "tcl_hdir"
PRECISION_AIRFLOW_BIT = 7
WIDE_AIRFLOW_BIT = 2

VERTICAL_OPTIONS = {
    0: "off",
    7: "full swing",
    8: "upper swing",
    9: "lower swing",
    1: "top fixed",
    2: "upper fixed",
    3: "middle fixed",
    4: "lower fixed",
    5: "bottom fixed",
}
HORIZONTAL_OPTIONS = {
    0: "off",
    10: "full swing",
    11: "left swing",
    12: "middle swing",
    13: "right swing",
    1: "left fixed",
    2: "center-left fixed",
    3: "middle fixed",
    4: "center-right fixed",
    5: "right fixed",
}
WIDE_HORIZONTAL_OPTIONS = {
    14: "wide swing",
    15: "center-left swing",
    16: "center-right swing",
    6: "left wide fixed",
    7: "right wide fixed",
    8: "whole angle fixed",
}


def has_airflow_feature(state: dict[str, Any], bit: int) -> bool:
    """Return an explicitly advertised capability; -1 is unsupported."""
    flags = state.get("if_function")
    return type(flags) is int and flags >= 0 and bool(flags & (1 << bit))


def full_swing_code(state: dict[str, Any], param: str) -> int:
    """Distinguish basic swing (1) from precision vertical (7)/horizontal (10)."""
    if param == VERTICAL:
        flags = state.get("if_function")
        if type(flags) is int and flags >= 0:
            return 7 if has_airflow_feature(state, PRECISION_AIRFLOW_BIT) else 1
        # Preserve the pre-v0.4.6 command when capabilities are not reported.
        return 7
    if param == HORIZONTAL:
        return 10 if has_airflow_feature(state, PRECISION_AIRFLOW_BIT) else 1
    raise ValueError(f"Unknown airflow axis: {param}")


def airflow_options(state: dict[str, Any], param: str) -> dict[int, str]:
    """Expose only options advertised by this device's app profile."""
    full_swing = full_swing_code(state, param)
    if not has_airflow_feature(state, PRECISION_AIRFLOW_BIT):
        return {0: "off", full_swing: "full swing"}
    if param == VERTICAL:
        return dict(VERTICAL_OPTIONS)
    options = dict(HORIZONTAL_OPTIONS)
    if has_airflow_feature(state, WIDE_AIRFLOW_BIT):
        options.update(WIDE_HORIZONTAL_OPTIONS)
    return options


def airflow_command(state: dict[str, Any], param: str, option: str) -> dict[str, int]:
    """Build a single-axis command, rejecting unavailable options."""
    for code, label in airflow_options(state, param).items():
        if option == label:
            return {param: code}
    raise ValueError(f"Unsupported airflow option: {option}")


def is_swinging(state: dict[str, Any], param: str) -> bool:
    """Recognise full/restricted swing without confusing fixed positions."""
    label = airflow_options(state, param).get(state.get(param), "")
    return label.endswith(" swing")
