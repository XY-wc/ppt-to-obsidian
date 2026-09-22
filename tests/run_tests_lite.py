# -*- coding: utf-8 -*-
"""轻量跑 tests/test_*.py 里的 test_* 函数（无需 pytest）。"""
import os
import sys
import traceback
import importlib.util

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, os.path.join(ROOT, "tests"))

SKIP_MODULES = {"test_pdf_support"}  # 需要 pymupdf


def load_module(path):
    name = os.path.splitext(os.path.basename(path))[0]
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def main():
    tests_dir = os.path.join(ROOT, "tests")
    files = sorted(f for f in os.listdir(tests_dir) if f.startswith("test_") and f.endswith(".py"))
    passed = failed = skipped = 0
    errors = []
    for fn in files:
        mod_name = os.path.splitext(fn)[0]
        if mod_name in SKIP_MODULES:
            print(f"SKIP {fn} (missing dep)")
            skipped += 1
            continue
        path = os.path.join(tests_dir, fn)
        try:
            mod = load_module(path)
        except Exception:
            print(f"LOAD_FAIL {fn}")
            traceback.print_exc()
            failed += 1
            errors.append(fn)
            continue
        for name in dir(mod):
            if not name.startswith("test_"):
                continue
            func = getattr(mod, name)
            if not callable(func):
                continue
            try:
                func()
                print(f"PASS {fn}::{name}")
                passed += 1
            except Exception:
                print(f"FAIL {fn}::{name}")
                traceback.print_exc()
                failed += 1
                errors.append(f"{fn}::{name}")
    print(f"\nSUMMARY pass={passed} fail={failed} skip={skipped}")
    if errors:
        print("ERRORS:", ", ".join(errors))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
