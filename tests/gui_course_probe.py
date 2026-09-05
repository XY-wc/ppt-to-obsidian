import sys, os, tempfile, shutil
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

from gui.app_v3 import App
from core.app_config import AppPaths, AccountManager


def log(*a):
    print(*a)
    sys.stdout.flush()


tmp = tempfile.mkdtemp(prefix="wb_course_test_")
try:
    paths = AppPaths(base=tmp)
    app = App()
    app.paths = paths
    app.mgr = AccountManager(paths)
    app.mgr.register("test", "pass1234")
    app.account = app.mgr.login("test", "pass1234")
    app.switch_to_main()
    app.update_idletasks()

    rootdir = os.path.join(tmp, "MyVault")
    os.makedirs(rootdir, exist_ok=True)
    app._vault_root = rootdir
    app.new_course_var.set("药理学")
    app._new_course()
    log("active=", app._active_course, "vault=", app._vault_dir)
    log("created=", os.path.isdir(os.path.join(rootdir, "药理学")))
    log("entry=", app.vault_entry.get())

    app.new_course_var.set("高等数学")
    app._new_course()
    log("second active=", app._active_course, "vault=", app._vault_dir)
    app._select_course("药理学")
    log("switchback vault=", app._vault_dir, "entry=", app.vault_entry.get())
    log("courses=", [c["name"] for c in app._courses])

    # 持久化 + 恢复
    app.destroy()
    app2 = App()
    app2.paths = paths
    app2.mgr = AccountManager(paths)
    app2.account = app2.mgr.login("test", "pass1234")
    app2.switch_to_main()
    app2.update_idletasks()
    app2._load_courses()
    app2._refresh_course_ui()
    log("restored=", [c["name"] for c in app2._courses], "active=", app2._active_course)
    log("listbox size=", app2.course_lb.size())
    app2.destroy()
    log("PASS")
finally:
    shutil.rmtree(tmp, ignore_errors=True)
