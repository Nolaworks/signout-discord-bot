"""Pure selection and conflict rules for admin reservation blocks."""


def parse_tool_selection(selection: str) -> tuple[bool, list[str]]:
    """Return whether all tools were selected and the requested tool names."""
    raw = selection.strip()
    if raw.lower() in {"all", "__all__"}:
        return True, []

    names = (part.strip() for part in raw.split(","))
    return False, list(dict.fromkeys(name for name in names if name))


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