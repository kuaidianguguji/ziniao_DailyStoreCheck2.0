"""DeepSeek 店铺数据分析客户端。

本文件只负责读取 DeepSeek 配置、组装兼容 OpenAI Chat Completions 的请求、
发送 ALL_info，并返回模型生成的分析文本。飞书接收人和消息发送仍由编排器处理。
"""

from __future__ import annotations

import json
import logging
from typing import Any

import requests


LOGGER = logging.getLogger(__name__)



class DeepSeekClient:
    """使用配置中的 API Key 和系统提示词调用 DeepSeek。"""

    def __init__(self, config: dict[str, Any]):
        """读取 DeepSeek 独立配置，并创建可复用的 HTTP Session。"""
        deepseek_config = config.get("deepseek", {})
        if not isinstance(deepseek_config, dict):
            deepseek_config = {}

        # enabled 可临时关闭 AI 分析；默认开启，API Key 为空时仍会安全跳过。
        enabled_value = deepseek_config.get("enabled", True)
        self.enabled = str(enabled_value).strip().lower() not in {"false", "0", "no", "off", "关闭"}
        self.api_key = str(deepseek_config.get("api_key") or "").strip()
        self.model_name = str(deepseek_config.get("model_name") or "deepseek-v4-flash").strip()
        self.base_url = str(deepseek_config.get("base_url") or "https://api.deepseek.com/v1").rstrip("/")
        self.system_prompt = str(deepseek_config.get("system_prompt") or "").strip()
        self.temperature = float(deepseek_config.get("temperature", 0.7))
        self.max_tokens = int(deepseek_config.get("max_tokens", 2000))
        self.timeout_seconds = int(deepseek_config.get("timeout_seconds", 120))
        self.session = requests.Session()

    @property
    def configured(self) -> bool:
        """只有开关、API Key、模型和系统提示词均有效时才允许发送请求。"""
        return bool(
            self.enabled
            and self.api_key
            and self.model_name
            and self.base_url
            and self.system_prompt
        )

    def analyze_all_info(self, all_info: list[dict[str, Any]]) -> str:
        """把本轮所有店铺数据作为 JSON 文本发给 DeepSeek，并返回分析结果。"""
        if not all_info:
            LOGGER.info("[DeepSeek][跳过] ALL_info 为空，不发送分析请求")
            return ""
        if not self.enabled:
            LOGGER.info("[DeepSeek][跳过] deepseek.enabled=false")
            return ""
        if not self.configured:
            missing_fields: list[str] = []
            if not self.api_key:
                missing_fields.append("api_key")
            if not self.model_name:
                missing_fields.append("model_name")
            if not self.base_url:
                missing_fields.append("base_url")
            if not self.system_prompt:
                missing_fields.append("system_prompt")
            LOGGER.warning("[DeepSeek][跳过] 配置不完整，缺少=%s", missing_fields)
            return ""

        # ALL_info 本身是列表/字典结构，使用 JSON 序列化可防止字段和店铺之间发生歧义。
        user_text = json.dumps(all_info, ensure_ascii=False, indent=2, default=str)
        request_body = {
            "model": self.model_name,
            "messages": [
                {"role": "system", "content": self.system_prompt},
                {"role": "user", "content": user_text},
            ],
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        request_url = f"{self.base_url}/chat/completions"
        LOGGER.info(
            "[DeepSeek][请求准备] url=%s，model=%s，店铺数=%s，ALL_info字符数=%s，temperature=%s，max_tokens=%s",
            request_url,
            self.model_name,
            len(all_info),
            len(user_text),
            self.temperature,
            self.max_tokens,
        )
        LOGGER.info("[DeepSeek][ALL_info输入] %s", json.dumps(all_info, ensure_ascii=False, default=str))

        try:
            response = self.session.post(
                request_url,
                headers=headers,
                json=request_body,
                timeout=self.timeout_seconds,
            )
            response.raise_for_status()
        except requests.RequestException as exc:
            response_text = ""
            if getattr(exc, "response", None) is not None:
                response_text = str(exc.response.text or "")[:1000]
            LOGGER.exception("[DeepSeek][HTTP失败] 异常=%s，response=%r", exc, response_text)
            raise RuntimeError(f"DeepSeek HTTP 请求失败: {exc}") from exc

        try:
            response_json = response.json()
        except ValueError as exc:
            LOGGER.error("[DeepSeek][响应解析失败] 返回内容不是 JSON：%r", str(response.text or "")[:1000])
            raise RuntimeError("DeepSeek 返回内容不是有效 JSON") from exc

        try:
            answer = str(response_json["choices"][0]["message"]["content"] or "").strip()
        except (KeyError, IndexError, TypeError) as exc:
            LOGGER.error("[DeepSeek][响应结构错误] response=%s", json.dumps(response_json, ensure_ascii=False, default=str))
            raise RuntimeError("DeepSeek 响应缺少 choices[0].message.content") from exc
        if not answer:
            raise RuntimeError("DeepSeek 返回的分析文本为空")

        LOGGER.info("[DeepSeek][分析成功] 返回字符数=%s，分析结果=%r", len(answer), answer)
        return answer
