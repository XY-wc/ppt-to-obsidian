# -*- coding: utf-8 -*-
"""
大模型客户端层 —— 用户"绑定自己的大模型 API"后调用。

设计:
  - 支持任意 **OpenAI 兼容** 端点(OpenAI / DeepSeek / Moonshot / 智谱 / Ollama 等),
    只需 base_url + api_key + model 即可, 不写死任何厂商。
  - 优先走 openai SDK; 若未安装则退化为纯 requests 直连 chat/completions,
    保证低依赖也能跑。
  - chat() 返回纯文本; 便于上层把"专业知识注入 prompt"后调用。
"""
from typing import List, Dict, Optional, Generator

# 常见厂商速选(供界面下拉, 未列出的可填自定义 base_url)
# 每条: label/base_url/model, 可选 auth_header/auth_scheme(默认标准 Bearer)
KNOWN_PROVIDERS = [
    {"label": "DeepSeek", "base_url": "https://api.deepseek.com/v1", "model": "deepseek-chat"},
    # Dots(dots.ai): 深度思考模型。认证头用 api-key(Bearer 也可), 已实测两种都通。
    # base_url 需带 /v1(OpenAI 兼容客户端约定, SDK 会再拼 /chat/completions);
    # host + 模型 dots3-note-prev 已用真实 key 验证可用。
    # disable_thinking=true → 关掉思考(reasoning), 更快更省、直接给最终 content(适合做笔记)。
    {"label": "Dots(dots.ai)",
     "base_url": "https://note3-prev-api.askdiandian.com/v1",
     "model": "dots3-note-prev",
     "auth_header": "api-key", "auth_scheme": "",
     "disable_thinking": True},
    {"label": "OpenAI", "base_url": "https://api.openai.com/v1", "model": "gpt-4o-mini"},
    {"label": "Moonshot(Kimi)", "base_url": "https://api.moonshot.cn/v1", "model": "moonshot-v1-8k"},
    {"label": "智谱GLM", "base_url": "https://open.bigmodel.cn/api/paas/v4", "model": "glm-4-flash"},
    {"label": "通义千问", "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1", "model": "qwen-plus"},
    {"label": "Ollama(本地)", "base_url": "http://localhost:11434/v1", "model": "qwen2.5:7b"},
    {"label": "自定义", "base_url": "", "model": ""},
]


class LLMError(RuntimeError):
    pass


def _get_client():
    """返回 openai 客户端类(已安装)或 None。"""
    try:
        from openai import OpenAI
        return OpenAI
    except Exception:
        return None


class LLMClient:
    def __init__(self, base_url: str, api_key: str, model: str,
                 temperature: float = 0.3, timeout: float = 120.0,
                 auth_header: str = "", auth_scheme: str = "",
                 disable_thinking: bool = False):
        self.base_url = (base_url or "").strip().rstrip("/")
        self.api_key = (api_key or "").strip()
        self.model = (model or "").strip()
        self.temperature = temperature
        self.timeout = timeout
        # 认证头控制。默认空 → 标准 OpenAI: 请求头 Authorization: Bearer <key>。
        # 部分厂商(如 Dots)用自定义头, 如 api-key: <key>, 此时填 auth_header="api-key", auth_scheme=""
        self.auth_header = (auth_header or "").strip() or "Authorization"
        self.auth_scheme = (auth_scheme or "").strip()
        # 深度思考模型(如 dots)默认先写 reasoning_content 再写 content, 会吃 max_tokens。
        # 为让结构化笔记更快更省, dots 预置默认关闭思考(enable_thinking=false)。
        self.disable_thinking = bool(disable_thinking)
        if not self.model:
            raise LLMError("未配置模型名(model)")

    def _auth_value(self) -> str:
        """构造认证头的值。标准 Bearer 走 'Bearer <key>'; 自定义(无 scheme)则裸 key。"""
        key = self.api_key or "EMPTY"
        if self.auth_header == "Authorization" and not self.auth_scheme:
            return f"Bearer {key}"
        return (f"{self.auth_scheme} {key}" if self.auth_scheme else key)

    def _custom_header(self) -> Dict[str, str]:
        """当使用自定义认证头(非标准 Authorization)时返回需附加的请求头, 否则空。"""
        if self.auth_header != "Authorization":
            return {self.auth_header: self._auth_value()}
        return {}

    # ---- 是否可用(本地直连模式无需 key, 允许空 key 如 Ollama) ----
    @property
    def needs_key(self) -> bool:
        return not (self.base_url.startswith("http://localhost") or self.base_url.startswith("http://127.0.0.1"))

    def _sdk_default_headers(self) -> Optional[Dict[str, str]]:
        """需要自定义认证头(非标准 Bearer)时, 返回传给 openai 客户端的 default_headers。

        标准 Authorization 场景交给 SDK 自动带 Bearer; 自定义头场景(dots 的 api-key)
        附加该头。注意 openai 仍会自动加 Authorization: Bearer, 多数网关会忽略未知头,
        故无需刻意移除; 若个别厂商因此报错, 在 auth_scheme='' 且 base_url 本地时再处理。
        """
        if self.auth_header == "Authorization":
            return None
        return self._custom_header()

    def _extra_body(self) -> Optional[Dict]:
        """返回需附加在请求 body 里的额外参数(如关闭深度思考)。"""
        if self.disable_thinking:
            return {"chat_template_kwargs": {"enable_thinking": False}}
        return None

    def chat(self, messages: List[Dict], max_tokens: int = 4096) -> str:
        """一次性返回回复文本。messages: [{'role','content'}, ...]"""
        client_cls = _get_client()
        payload = dict(temperature=self.temperature, max_tokens=max_tokens)
        eb = self._extra_body()
        if eb:
            payload["extra_body"] = eb
        try:
            if client_cls:
                client = client_cls(api_key=self.api_key or "EMPTY",
                                    base_url=self.base_url or None,
                                    timeout=self.timeout,
                                    default_headers=self._sdk_default_headers())
                resp = client.chat.completions.create(
                    model=self.model, messages=messages, **payload)
                return self._extract_content(resp.choices[0].message)
            else:
                return self._chat_requests(messages, max_tokens)
        except LLMError:
            raise
        except Exception as e:
            raise LLMError(f"模型调用失败: {e}")

    @staticmethod
    def _extract_content(message) -> str:
        """从 openai 响应 message 里取 content, 兼容思考型模型。

        若 content 为空但存在 reasoning_content, 说明输出可能被 max_tokens 截断
        在思考阶段, 抛出明确错误而不是静默返回空串。
        """
        content = (getattr(message, "content", None) or "").strip()
        if content:
            return content
        reasoning = getattr(message, "reasoning_content", None) or ""
        if reasoning:
            raise LLMError(
                "模型只返回了思考过程而没有最终内容(可能 max_tokens 被思考占用或输出被截断)。"
                "可尝试关闭深度思考或增大 max_tokens。"
            )
        return ""

    def _chat_requests(self, messages, max_tokens):
        """无 openai SDK 时的直连降级(requests)。"""
        try:
            import requests
        except Exception:
            raise LLMError("缺少 requests 库")
        url = f"{self.base_url}/chat/completions"
        headers = {"Content-Type": "application/json"}
        if self.auth_header == "Authorization":
            if self.api_key:
                headers["Authorization"] = f"Bearer {self.api_key}"
        else:
            headers[self.auth_header] = self._auth_value()
        body = {"model": self.model, "messages": messages,
                "temperature": self.temperature, "max_tokens": max_tokens}
        eb = self._extra_body()
        if eb:
            body.update(eb)
        try:
            r = requests.post(url, json=body, headers=headers, timeout=self.timeout)
        except Exception as e:
            raise LLMError(f"网络请求失败: {e}")
        if r.status_code != 200:
            raise LLMError(f"API 返回 {r.status_code}: {r.text[:300]}")
        data = r.json()
        try:
            msg = data["choices"][0]["message"]
            content = (msg.get("content") or "").strip()
            if not content and msg.get("reasoning_content"):
                raise LLMError(
                    "模型只返回了思考过程而没有最终内容(可能 max_tokens 被思考占用或输出被截断)。"
                    "可尝试关闭深度思考或增大 max_tokens。"
                )
            return content
        except LLMError:
            raise
        except Exception:
            raise LLMError(f"响应解析失败: {str(data)[:300]}")

    # ---- 流式(供GUI显示进度) ----
    def chat_stream(self, messages, max_tokens=4096) -> Generator[str, None, None]:
        """若支持流式则逐段产出文本; 否则一次性产出。"""
        client_cls = _get_client()
        if client_cls:
            client = client_cls(api_key=self.api_key or "EMPTY",
                                base_url=self.base_url or None,
                                timeout=self.timeout,
                                default_headers=self._sdk_default_headers())
            kwargs = dict(model=self.model, messages=messages,
                          temperature=self.temperature, max_tokens=max_tokens, stream=True)
            eb = self._extra_body()
            if eb:
                kwargs["extra_body"] = eb
            stream = client.chat.completions.create(**kwargs)
            for chunk in stream:
                if chunk.choices and chunk.choices[0].delta and chunk.choices[0].delta.content:
                    yield chunk.choices[0].delta.content
            return
        # 无 SDK -> 一次性
        yield self._chat_requests(messages, max_tokens)


def build_llm(m: Dict, **kw) -> LLMClient:
    """从账号模型字典(m)构造 LLMClient, 兼容旧字段、自定义认证头与思考开关。

    m 需含 base_url/api_key/model; 可选 auth_header/auth_scheme/disable_thinking。
    """
    md = m or {}
    # 显式优先, 避免被默认 False 覆盖传入的 disable_thinking
    if "disable_thinking" not in kw:
        kw["disable_thinking"] = bool(md.get("disable_thinking"))
    return LLMClient(
        md.get("base_url", ""),
        md.get("api_key", ""),
        md.get("model", ""),
        auth_header=md.get("auth_header", ""),
        auth_scheme=md.get("auth_scheme", ""),
        **kw,
    )


if __name__ == "__main__":
    print("可用厂商:", [p["label"] for p in KNOWN_PROVIDERS])
