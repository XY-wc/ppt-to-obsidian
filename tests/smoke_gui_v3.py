# -*- coding: utf-8 -*-
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

import tkinter as tk  # noqa: E402
from gui.app_v3 import App  # noqa: E402


class StubAccount:
    username = "tester"

    def get_pref(self, k, d=None):
        return d

    def get_models(self):
        return []

    def set_pref(self, k, v):
        pass

    def save(self):
        return True

    def remember_last_username(self):
        return "tester"


def main():
    app = App()
    app.account = StubAccount()
    app.switch_to_main()
    need = [
        "course_lb", "course_tree", "drop_zone", "model_dd", "mode_dd",
        "run_btn", "progress", "log_text", "result_path", "open_btn",
        "chat_text", "refine_entry", "refine_target_dd", "status_label",
        "statusbar_model", "vault_entry", "prof_buttons", "user_label",
        "root_path_lbl", "course_hint", "course_browse_hint", "model_hint",
        "set_root_btn", "ppt_file_label", "run_hint", "_ready_vars",
    ]
    missing = [n for n in need if not hasattr(app, n)]
    print("MISSING", missing if missing else "NONE")
    print("READY_KEYS", sorted(getattr(app, "_ready_vars", {}) or {}))
    print("READY_STATE", {k: v.get() for k, v in (app._ready_vars or {}).items()})
    print("PROF_COUNT", len(app.prof_buttons))
    print("HAS_MENU", bool(app["menu"]))
    app.update_idletasks()
    app.update()
    print("GEOM", app.winfo_width(), app.winfo_height())
    app.destroy()
    print("GUI_SMOKE_OK")


if __name__ == "__main__":
    main()
