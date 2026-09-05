# -*- coding: utf-8 -*-
"""认证头支持验证: 标准 OpenAI(Bearer) 与 自定义头(dots 的 api-key)。"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

from llm.client import LLMClient, build_llm


def test_standard_bearer():
    llm = LLMClient("https://api.deepseek.com/v1", "sk-abc", "deepseek-chat")
    assert llm.auth_header == "Authorization"
    assert llm._auth_value() == "Bearer sk-abc"
    # 标准认证: 不需要额外 default_headers(交给 SDK)
    assert llm._sdk_default_headers() is None


def test_dots_custom_header():
    llm = build_llm({
        "base_url": "https://note3-prev-api.askdiandian.com/v1",
        "api_key": "KEY123",
        "model": "dots3-note-prev",
        "auth_header": "api-key",
        "auth_scheme": "",
        "disable_thinking": True,
    })
    assert llm.auth_header == "api-key"
    assert llm._auth_value() == "KEY123"
    assert llm._sdk_default_headers() == {"api-key": "KEY123"}
    # 思考型模型应默认关闭思考, 并附加对应 body 参数
    assert llm.disable_thinking is True
    assert llm._extra_body() == {"chat_template_kwargs": {"enable_thinking": False}}


def test_disable_thinking_default_off():
    llm = build_llm({"base_url": "https://api.deepseek.com/v1", "api_key": "k", "model": "m"})
    assert llm.disable_thinking is False
    assert llm._extra_body() is None


def test_requests_fallback_header_injection():
    """无 openai SDK 时直连 requests, 应发送正确认证头。"""
    import sys as _sys
    captured = {}

    class FakeResp:
        status_code = 200
        text = ""
        def json(self):
            return {"choices": [{"message": {"content": "ok"}}]}

    def fake_post(url, json=None, headers=None, timeout=None):
        captured["url"] = url
        captured["headers"] = dict(headers or {})
        captured["body"] = json
        return FakeResp()

    fake_req = type("R", (), {"post": staticmethod(fake_post)})()
    real = _sys.modules.get("requests")
    _sys.modules["requests"] = fake_req
    try:
        # dots 风格
        llm = build_llm({"base_url": "https://note3-prev-api.askdiandian.com/v1",
                         "api_key": "K", "model": "m", "auth_header": "api-key",
                         "disable_thinking": True})
        llm._chat_requests([{"role": "user", "content": "hi"}], 10)
        assert captured["headers"]["api-key"] == "K"
        assert "Authorization" not in captured["headers"], "自定义头场景不应发 Bearer"
        assert captured["url"] == "https://note3-prev-api.askdiandian.com/v1/chat/completions"
        # 思考关闭参数应注入 body
        assert captured["body"].get("chat_template_kwargs", {}).get("enable_thinking") is False

        # 标准风格
        captured.clear()
        llm2 = LLMClient("https://api.deepseek.com/v1", "sk-x", "deepseek-chat")
        llm2._chat_requests([{"role": "user", "content": "hi"}], 10)
        assert captured["headers"]["Authorization"] == "Bearer sk-x"
    finally:
        if real is not None:
            _sys.modules["requests"] = real
        else:
            _sys.modules.pop("requests", None)


def test_build_llm_legacy_without_auth_fields():
    # 旧记录没有 auth_header, 应回退标准 Bearer
    llm = build_llm({"base_url": "https://x/v1", "api_key": "k", "model": "m"})
    assert llm.auth_header == "Authorization"
    assert llm._auth_value() == "Bearer k"


def test_account_stores_disable_thinking_roundtrip():
    import tempfile
    from core.app_config import AppPaths, Account
    tmp = tempfile.mkdtemp()
    paths = AppPaths(base=tmp)
    acc = Account("tester", paths)
    acc.add_model("Dots", "https://note3-prev-api.askdiandian.com/v1",
                  "k", "dots3-note-prev",
                  auth_header="api-key", auth_scheme="", disable_thinking=True)
    saved = acc.get_models()[0]
    assert saved["disable_thinking"] is True
    assert saved["auth_header"] == "api-key"
    # 重新从磁盘读, 验证持久化
    acc2 = Account("tester", paths)
    saved2 = acc2.get_models()[0]
    assert saved2["disable_thinking"] is True
    # 经由 build_llm 构造也应带上开关
    llm = build_llm(saved2)
    assert llm.disable_thinking is True


if __name__ == "__main__":
    test_standard_bearer(); print("✓ test_standard_bearer")
    test_dots_custom_header(); print("✓ test_dots_custom_header")
    test_disable_thinking_default_off(); print("✓ test_disable_thinking_default_off")
    test_requests_fallback_header_injection(); print("✓ test_requests_fallback_header_injection")
    test_build_llm_legacy_without_auth_fields(); print("✓ test_build_llm_legacy_without_auth_fields")
    test_account_stores_disable_thinking_roundtrip(); print("✓ test_account_stores_disable_thinking_roundtrip")
    print("\n全部通过")
