"""Gate matrix: the deny triggers' inert and drawn flags, which the attended acceptance run draws from."""

from __future__ import annotations

from pathlib import Path

from . import _cursor_gate_matrix_helpers as gate_matrix_helpers
from ._cursor_gate_matrix_helpers import TRIGGER_TOOL_CLASSES, TRIGGERS, Trigger, World, _const

# The fixture lives once in the helper module. An import would trip F811 on every test parameter that names it.
world = gate_matrix_helpers.world


# --- the inert and drawn flags ---

# The attended acceptance run draws one trigger per class. Each name is written out, so an empty, short or renamed set fails.
DRAWN_TRIGGER_NAMES_BY_CLASS = {
    "Shell": "cat-credentials",
    "Read": "credentials-file",
    "Write-create": "create-in-the-main-checkout",
    "Write-overwrite": "overwrite-in-the-main-checkout",
    "Task": "wait-only",
}


def trigger_flag_failures(table: dict[str, tuple[Trigger, ...]]) -> list[str]:
    """What is wrong with a trigger table's flags: a drawn trigger that is not inert, and a drawn set that is not exactly one
    named trigger per class."""
    triggers = [trigger for triggers_of_script in table.values() for trigger in triggers_of_script]
    failures = [f"{trigger.name} is drawn and not inert" for trigger in triggers if trigger.drawn and not trigger.inert]
    drawn: dict[str, list[str]] = {}
    for trigger in triggers:
        if trigger.drawn:
            drawn.setdefault(trigger.tool_class, []).append(trigger.name)
    expected = {tool_class: [name] for tool_class, name in DRAWN_TRIGGER_NAMES_BY_CLASS.items()}
    if drawn != expected:
        failures.append(f"the drawn set is {drawn}, not {expected}")
    return failures


def _trigger_for_table(name: str, tool: str, *, inert: bool, drawn: bool, overwrites: str | None = None) -> Trigger:
    return Trigger(name, tool, _const({}), _const({}), inert=inert, drawn=drawn, overwrites=overwrites)


class TestTriggerFlags:
    def test_no_drawn_trigger_is_missing_its_inert_flag_and_the_drawn_set_is_the_named_one_per_class(self) -> None:
        assert trigger_flag_failures(TRIGGERS) == []
        assert set(DRAWN_TRIGGER_NAMES_BY_CLASS) == TRIGGER_TOOL_CLASSES

    def test_a_table_with_a_drawn_trigger_that_is_not_inert_fails_the_predicate(self) -> None:
        table = {key: tuple(triggers) for key, triggers in TRIGGERS.items()}
        table["block-gh-pr-merge.sh"] = (_trigger_for_table("merge", "Shell", inert=False, drawn=True),)
        assert any("merge is drawn and not inert" in failure for failure in trigger_flag_failures(table))

    def test_a_table_with_an_empty_drawn_set_fails_the_predicate(self) -> None:
        table = {
            key: tuple(
                _trigger_for_table(t.name, t.tool, inert=t.inert, drawn=False, overwrites=t.overwrites) for t in triggers
            )
            for key, triggers in TRIGGERS.items()
        }
        assert trigger_flag_failures(table)

    def test_a_table_with_two_drawn_triggers_in_one_class_fails_the_predicate_whichever_comes_first(self) -> None:
        extra_shell_trigger = _trigger_for_table("second-shell", "Shell", inert=True, drawn=True)
        before_the_named_one = {key: tuple(triggers) for key, triggers in TRIGGERS.items()}
        before_the_named_one["block-gh-pr-merge.sh"] = (extra_shell_trigger,)
        after_the_named_one = {**TRIGGERS, "zz-appended.sh": (extra_shell_trigger,)}
        for table in (before_the_named_one, after_the_named_one):
            assert any("second-shell" in failure for failure in trigger_flag_failures(table))

    def test_the_drawn_write_triggers_target_files_under_the_fixture_repository_only(self, world: World) -> None:
        all_triggers = [trigger for triggers in TRIGGERS.values() for trigger in triggers]
        drawn_writes = [trigger for trigger in all_triggers if trigger.drawn and trigger.tool == "Write"]
        assert {trigger.name for trigger in drawn_writes} == {"create-in-the-main-checkout", "overwrite-in-the-main-checkout"}
        for trigger in drawn_writes:
            target = Path(trigger.deny_input(world)["file_path"]).resolve()
            assert target.is_relative_to(world.repo), trigger.name
            assert world.repo.is_relative_to(world.base.resolve()), trigger.name

    def test_a_table_with_a_renamed_drawn_trigger_fails_the_predicate(self) -> None:
        table = {key: tuple(triggers) for key, triggers in TRIGGERS.items()}
        table["deny-no-op-dispatch.sh"] = (_trigger_for_table("renamed", "Task", inert=True, drawn=True),)
        assert trigger_flag_failures(table)
