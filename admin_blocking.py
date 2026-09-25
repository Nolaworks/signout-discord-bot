"""Pure selection and conflict rules for admin reservation blocks."""


def parse_tool_selection(selection: str | list[str | None]) -> tuple[bool, list[str]]:
    """Return whether all tools were selected and the independently selected names."""
    values = selection.split(",") if isinstance(selection, str) else selection
    values = [value.strip() for value in values if value and value.strip()]
    all_tools_selected = any(value.lower() in {"all", "__all__"} for value in values)
    names = [value for value in values if value.lower() not in {"all", "__all__"}]
    return all_tools_selected, list(dict.fromkeys(names))


def should_skip_for_conflicts(
    conflicts: list,
    all_tools_selected: bool,
    force: bool,
    currently_active: bool = False,
) -> bool:
    """All-tools blocks are conservative; force only applies to future conflicts."""
    if currently_active:
        return True
    return bool(conflicts) and (all_tools_selected or not force)