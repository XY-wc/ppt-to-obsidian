# -*- coding: utf-8 -*-
"""
本地配置 / 账号 / 模型API凭据存储层。

local-first 设计:
  - 所有数据存于应用数据目录 <vault根>/.ppt2obsidian/ 之下, 不依赖中心服务器。
  - "账号" = 本地档案 (多账号可选, 便于未来迁移)。密码经加盐 SHA-256 摘要存储,
    不做明文保存(本地应用, 安全基线; 若上云需换用更强的服务端方案)。
  - "绑定大模型 API" = 记录若干 OpenAI 兼容端点 (provider/base_url/key/model),
    仅本地明文保存在配置文件, 并标注其为本机敏感数据。

结构:
  <base_dir>/
    config.json                 # 全局设置 + 最近账号
    accounts/<username>.json    # 每个账号的档案 + 其绑定的模型
"""
import hashlib
import json
import os
import secrets
import string
from typing import List, Dict, Optional

BASE_DIR_NAME = ".ppt2obsidian"
CONFIG_NAME = "config.json"
ACCOUNTS_SUB = "accounts"


def _sha256_salted(salt: str, raw: str) -> str:
    return hashlib.sha256((salt + "::" + raw).encode("utf-8")).hexdigest()


class AppPaths:
    """集中管理应用数据目录。"""

    def __init__(self, base: Optional[str] = None):
        if base:
            self.base = base
        else:
            self.base = os.path.join(os.path.expanduser("~"), BASE_DIR_NAME)
        self.config_file = os.path.join(self.base, CONFIG_NAME)
        self.accounts_dir = os.path.join(self.base, ACCOUNTS_SUB)
        os.makedirs(self.accounts_dir, exist_ok=True)

    def account_file(self, username: str) -> str:
        return os.path.join(self.accounts_dir, f"{username}.json")


def _load_json(path: str, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def _save_json(path: str, data) -> bool:
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return True
    except Exception:
        return False


class Account:
    """一个本地账号档案。"""

    def __init__(self, username: str, paths: AppPaths):
        self.username = username
        self.paths = paths
        self.file = paths.account_file(username)
        self.data: Dict = _load_json(self.file, {})

    def exists(self) -> bool:
        return bool(self.data)

    # ---- 密码 ----
    def set_password(self, raw: str) -> str:
        salt = "".join(secrets.choice(string.ascii_letters + string.digits) for _ in range(16))
        self.data["password_salt"] = salt
        self.data["password_hash"] = _sha256_salted(salt, raw)
        return salt

    def verify_password(self, raw: str) -> bool:
        salt = self.data.get("password_salt", "")
        h = self.data.get("password_hash", "")
        return bool(salt) and _sha256_salted(salt, raw) == h

    def has_password(self) -> bool:
        return bool(self.data.get("password_hash"))

    # ---- 绑定的模型 (OpenAI 兼容端点) ----
    def set_models(self, models: List[Dict]):
        self.data["models"] = models
        self.save()

    def get_models(self) -> List[Dict]:
        return list(self.data.get("models", []))

    def add_model(self, name: str, base_url: str, api_key: str, model: str,
                  auth_header: str = "", auth_scheme: str = "",
                  disable_thinking: bool = False) -> Dict:
        m = {
            "name": name,
            "base_url": (base_url or "").strip().rstrip("/"),
            "api_key": (api_key or "").strip(),
            "model": (model or "").strip(),
            # 认证方式: 默认空=标准 OpenAI Authorization: Bearer;
            # 自定义厂商(如 dots)可填 auth_header="api-key"、auth_scheme=""。
            "auth_header": (auth_header or "").strip(),
            "auth_scheme": (auth_scheme or "").strip(),
            # 深度思考开关: dots 等思考型模型建议 True(关闭思考, 更快拿 content)。
            "disable_thinking": bool(disable_thinking),
        }
        models = self.get_models()
        models.append(m)
        self.set_models(models)
        return m

    def update_model(self, index: int, **fields):
        models = self.get_models()
        if 0 <= index < len(models):
            for k, v in fields.items():
                if v is not None:
                    models[index][k] = (bool(v) if k == "disable_thinking"
                                        else (str(v).strip() if not isinstance(v, bool) else v))
            self.set_models(models)

    def remove_model(self, index: int) -> bool:
        models = self.get_models()
        if 0 <= index < len(models):
            models.pop(index)
            self.set_models(models)
            return True
        return False

    # ---- 界面偏好 ----
    def set_pref(self, key: str, value):
        self.data.setdefault("prefs", {})[key] = value
        self.save()

    def get_pref(self, key: str, default=None):
        return self.data.get("prefs", {}).get(key, default)

    def save(self) -> bool:
        return _save_json(self.file, self.data)


class AccountManager:
    def __init__(self, paths: Optional[AppPaths] = None):
        self.paths = paths or AppPaths()
        self._current: Optional[str] = None

    def all_usernames(self) -> List[str]:
        names = []
        if os.path.isdir(self.paths.accounts_dir):
            for fn in sorted(os.listdir(self.paths.accounts_dir)):
                if fn.endswith(".json"):
                    names.append(fn[:-5])
        return names

    def get_account(self, username: str) -> Account:
        return Account(username, self.paths)

    def register(self, username: str, password: str) -> Account:
        acc = Account(username.strip(), self.paths)
        if acc.exists():
            raise ValueError(f"账号 {username} 已存在")
        if not username.strip():
            raise ValueError("用户名不能为空")
        if len(password) < 4:
            raise ValueError("密码至少 4 位")
        acc.set_password(password)
        acc.data.setdefault("created_at", __import__("datetime").datetime.now().isoformat())
        acc.save()
        return acc

    def login(self, username: str, password: str) -> Account:
        acc = Account(username.strip(), self.paths)
        if not acc.exists():
            raise ValueError("账号不存在")
        if not acc.verify_password(password):
            raise ValueError("密码错误")
        self._current = acc.username
        # 记住最近登录
        cfg = _load_json(self.paths.config_file, {})
        cfg["last_username"] = acc.username
        _save_json(self.paths.config_file, cfg)
        return acc

    def current_account(self) -> Optional[Account]:
        if not self._current:
            return None
        return Account(self._current, self.paths)

    def remember_last_username(self) -> Optional[str]:
        cfg = _load_json(self.paths.config_file, {})
        return cfg.get("last_username")


if __name__ == "__main__":
    import tempfile
    tmp = tempfile.mkdtemp()
    mgr = AccountManager(AppPaths(tmp))
    acc = mgr.register("demo", "1234")
    acc.add_model("DeepSeek", "https://api.deepseek.com/v1", "sk-xxxx", "deepseek-chat")
    print("账号创建:", acc.username, "验证密码:", acc.verify_password("1234"))
    print("绑定模型:", acc.get_models())
    mgr2 = AccountManager(AppPaths(tmp))
    a2 = mgr2.login("demo", "1234")
    print("重新登录成功, 模型:", a2.get_models())
