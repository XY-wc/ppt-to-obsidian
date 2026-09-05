import sys, os, tempfile, shutil
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

from gui.app_v3 import App
from core.app_config import AppPaths, AccountManager
from pipeline import run_conversion


def log(*a):
    print(*a)
    sys.stdout.flush()


tmp = tempfile.mkdtemp(prefix="wb_course_e2e_")
try:
    paths = AppPaths(base=tmp)
    vault_root = os.path.join(tmp, "MyVault")
    os.makedirs(os.path.join(vault_root, ".obsidian"), exist_ok=True)

    app = App()
    app.paths = paths
    app.mgr = AccountManager(paths)
    app.mgr.register("test", "pass1234")
    app.account = app.mgr.login("test", "pass1234")
    app.switch_to_main()
    app.update_idletasks()

    app._vault_root = vault_root
    app.new_course_var.set("药理学")
    app._new_course()
    course_dir = app._vault_dir
    log("course dir=", course_dir, "exists=", os.path.isdir(course_dir))
    log("vault_entry 已同步=", app.vault_entry.get() == course_dir)

    # 直接走 pipeline 离线生成, 确认落到课程夹(vault 根之下)
    ppt = os.path.abspath("outputs/sample_pharmacy.pptx")
    res = run_conversion(ppt, "pharmacy", app._vault_dir, account=app.account,
                         model_index=0, llm=None, progress=lambda m: None)
    log("conversion ok=", res.ok)
    md = []
    for root, _, fs in os.walk(course_dir):
        for f in fs:
            if f.endswith(".md"):
                md.append(f)
    log("课程夹内 md=", md)

    # 关键: 课程夹是 vault_root 子目录, 应被 _inside_real_vault 判定为真(不弹窗)
    log("inside_real_vault(course)=", app._inside_real_vault(course_dir))
    # 反向: 无关目录应判假
    outside = os.path.join(tmp, "NotAVault")
    os.makedirs(outside, exist_ok=True)
    log("inside_real_vault(outside)=", app._inside_real_vault(outside))
    app.destroy()
    log("PASS")
finally:
    shutil.rmtree(tmp, ignore_errors=True)
