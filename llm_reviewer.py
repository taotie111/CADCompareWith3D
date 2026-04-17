import json
import os
import urllib.error
import urllib.request
from typing import Any, Dict, List


class LLMReviewer:
    """
    LLM复核器：支持OpenAI兼容 /chat/completions 接口。
    - 若远端调用失败，自动回退到规则引擎说明。
    - 若提供 image_urls，会按多模态消息结构发送。
    """

    def __init__(self, enabled: bool = False, provider: Dict[str, Any] | None = None) -> None:
        self.enabled = enabled
        self.provider = provider or {}

    def review_event(self, event: Dict[str, Any]) -> Dict[str, Any]:
        if not self.enabled:
            return self._fallback(event, "llm_disabled")

        try:
            response = self._call_remote_llm(event)
            parsed = self._parse_response(response)
            if not parsed.get("schema_valid", False):
                return self._fallback(event, "schema_invalid")
            return parsed
        except Exception as exc:  # noqa: BLE001
            return self._fallback(event, f"remote_error:{type(exc).__name__}")

    def _call_remote_llm(self, event: Dict[str, Any]) -> Dict[str, Any]:
        base_url = str(self.provider.get("base_url", "")).rstrip("/")
        endpoint_path = str(self.provider.get("endpoint_path", "/chat/completions"))
        endpoint = f"{base_url}{endpoint_path}"

        if not base_url:
            raise ValueError("llm provider base_url is empty")

        api_key = str(self.provider.get("api_key", "")).strip()
        if not api_key:
            env_name = str(self.provider.get("api_key_env", "LLM_API_KEY"))
            api_key = str(os.getenv(env_name, "")).strip()
        if not api_key:
            raise ValueError("llm api key is empty")

        model = str(self.provider.get("model", "qianwen-3.5-plus"))
        timeout_seconds = int(self.provider.get("timeout_seconds", 30))

        payload = {
            "model": model,
            "temperature": 0.1,
            "response_format": {"type": "json_object"},
            "messages": self._build_messages(event),
        }

        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            endpoint,
            data=body,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
            },
        )

        with urllib.request.urlopen(req, timeout=timeout_seconds) as resp:
            raw = resp.read().decode("utf-8")
            return json.loads(raw)

    def _build_messages(self, event: Dict[str, Any]) -> List[Dict[str, Any]]:
        evidence = event.get("evidence", {}) or {}
        image_urls = evidence.get("image_urls", []) or []

        system_prompt = (
            "你是水利工程风险复核助手。"
            "你必须输出严格JSON，字段包括："
            "risk_interpretation,evidence_refs,confidence_delta,recommended_action,schema_valid。"
            "不要编造规范编号，不要输出JSON以外文本。"
        )

        user_payload = {
            "event": {
                "rule_id": event.get("rule_id"),
                "risk_type": event.get("risk_type"),
                "level": event.get("level"),
                "location": event.get("location"),
                "source_type": event.get("source_type"),
                "source_confidence": event.get("source_confidence"),
                "match_confidence": event.get("match_confidence"),
                "suggestion": event.get("suggestion"),
            },
            "evidence": evidence,
            "task": "请进行风险复核并给出处置建议。",
        }

        if image_urls:
            content: List[Dict[str, Any]] = [{"type": "text", "text": json.dumps(user_payload, ensure_ascii=False)}]
            for url in image_urls:
                content.append({"type": "image_url", "image_url": {"url": str(url)}})
            return [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": content},
            ]

        return [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False)},
        ]

    def _parse_response(self, response: Dict[str, Any]) -> Dict[str, Any]:
        choices = response.get("choices", []) or []
        if not choices:
            return {"schema_valid": False}

        message = choices[0].get("message", {}) or {}
        content = message.get("content", "")

        parsed: Dict[str, Any]
        if isinstance(content, str):
            try:
                parsed = json.loads(content)
            except json.JSONDecodeError:
                return {"schema_valid": False}
        elif isinstance(content, dict):
            parsed = content
        else:
            return {"schema_valid": False}

        schema_valid = all(k in parsed for k in ["risk_interpretation", "evidence_refs", "confidence_delta", "recommended_action"])

        return {
            "review_source": "llm",
            "risk_interpretation": parsed.get("risk_interpretation", ""),
            "evidence_refs": parsed.get("evidence_refs", []),
            "confidence_delta": float(parsed.get("confidence_delta", 0.0) or 0.0),
            "recommended_action": parsed.get("recommended_action", "现场复核"),
            "schema_valid": bool(parsed.get("schema_valid", schema_valid)),
            "fallback_used": False,
        }

    def _fallback(self, event: Dict[str, Any], reason: str) -> Dict[str, Any]:
        return {
            "review_source": "rule_engine",
            "risk_interpretation": f"LLM复核不可用（{reason}），采用规则引擎结论。",
            "evidence_refs": [],
            "confidence_delta": 0.0,
            "recommended_action": event.get("suggestion", "现场复核"),
            "schema_valid": True,
            "fallback_used": True,
            "fallback_reason": reason,
        }
