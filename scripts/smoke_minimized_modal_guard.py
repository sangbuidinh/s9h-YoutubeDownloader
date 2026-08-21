import sys
import tkinter as tk
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from ui.dialogs import _restore_iconic_parent_for_modal


class _FakeParent:
    def __init__(self, state_value="normal", *, raise_state=False):
        self.state_value = state_value
        self.raise_state = raise_state
        self.calls: list[str] = []

    def state(self):
        self.calls.append("state")
        if self.raise_state:
            raise tk.TclError("window state unavailable")
        return self.state_value

    def deiconify(self):
        self.calls.append("deiconify")
        self.state_value = "normal"

    def update_idletasks(self):
        self.calls.append("update_idletasks")

    def lift(self):
        self.calls.append("lift")


def main() -> int:
    minimized = _FakeParent("iconic")
    restored = _restore_iconic_parent_for_modal(minimized, True)
    _assert(restored, "iconic modal parent was not restored")
    _assert(
        minimized.calls == ["state", "deiconify", "update_idletasks", "lift"],
        f"unexpected iconic restore sequence: {minimized.calls}",
    )
    _assert(minimized.state_value == "normal", "iconic parent remained minimized")

    normal = _FakeParent("normal")
    _assert(not _restore_iconic_parent_for_modal(normal, True), "normal parent was unnecessarily restored")
    _assert(normal.calls == ["state"], f"normal parent was mutated: {normal.calls}")

    non_modal = _FakeParent("iconic")
    _assert(not _restore_iconic_parent_for_modal(non_modal, False), "non-modal dialog restored its parent")
    _assert(non_modal.calls == [], f"non-modal parent state was queried: {non_modal.calls}")

    unavailable = _FakeParent("iconic", raise_state=True)
    _assert(
        not _restore_iconic_parent_for_modal(unavailable, True),
        "state lookup failure should fail closed without changing the parent",
    )
    _assert(unavailable.calls == ["state"], f"state failure mutated parent: {unavailable.calls}")

    print("minimized modal guard smoke passed")
    return 0


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


if __name__ == "__main__":
    raise SystemExit(main())
