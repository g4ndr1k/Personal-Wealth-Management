from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_GOLDEN_FIXTURE = REPO_ROOT / "agent" / "tests" / "fixtures" / "rule_ai_golden_prompts.json"

DRAFT_PATH = "/api/mail/rules/ai/draft"
SAVE_RULE_PATH = "/api/mail/rules"

BLOCKED_ACTIONS = {
    "delete",
    "move_to_folder",
    "add_label",
    "mark_read",
    "mark_unread",
    "move_to_spam",
    "send_imessage",
    "forward",
    "auto_reply",
    "unsubscribe",
    "external_webhook",
    "route_to_pdf_pipeline",
    "skip_ai_inference",
    "stop_processing",
}


@dataclass(frozen=True)
class GoldenPrompt:
    id: str
    prompt: str
    mode: str
    expected_domain: str
    expected_safety_status: str
    expected_action_type: str
    expected_target: str
    expected_keywords_any: list[str]

    @classmethod
    def from_dict(cls, item: dict[str, Any], index: int) -> "GoldenPrompt":
        for key in (
            "id",
            "prompt",
            "expected_domain",
            "expected_safety_status",
            "expected_action_type",
            "expected_target",
            "expected_keywords_any",
        ):
            if key not in item:
                raise ValueError(f"Fixture entry {index} missing {key}")
        keywords = item["expected_keywords_any"]
        if not isinstance(keywords, list) or not keywords:
            raise ValueError(f"Fixture entry {item['id']} requires expected_keywords_any")
        return cls(
            id=str(item["id"]),
            prompt=str(item["prompt"]),
            mode=str(item.get("mode") or "alert_rule"),
            expected_domain=str(item["expected_domain"]),
            expected_safety_status=str(item["expected_safety_status"]),
            expected_action_type=str(item["expected_action_type"]),
            expected_target=str(item["expected_target"]),
            expected_keywords_any=[str(keyword) for keyword in keywords],
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class GoldenProbeResult:
    id: str
    prompt: str
    passed: bool
    expected_domain: str
    actual_domain: str | None = None
    safety_status: str | None = None
    saveable: bool | None = None
    action_type: str | None = None
    target: str | None = None
    errors: list[str] | None = None
    warnings: list[str] | None = None
    response_status: str | None = None
    status_code: int | None = None

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["errors"] = data["errors"] or []
        data["warnings"] = data["warnings"] or []
        return data


def load_golden_prompts(path: str | Path = DEFAULT_GOLDEN_FIXTURE) -> list[GoldenPrompt]:
    fixture_path = Path(path)
    data = json.loads(fixture_path.read_text())
    if not isinstance(data, list):
        raise ValueError("Golden prompt fixture must be a JSON array")
    prompts: list[GoldenPrompt] = []
    seen_ids: set[str] = set()
    for index, item in enumerate(data):
        if not isinstance(item, dict):
            raise ValueError(f"Fixture entry {index} must be an object")
        prompt = GoldenPrompt.from_dict(item, index)
        if prompt.id in seen_ids:
            raise ValueError(f"Duplicate prompt id: {prompt.id}")
        seen_ids.add(prompt.id)
        prompts.append(prompt)
    return prompts


def select_golden_prompts(
    prompts: list[GoldenPrompt],
    prompt_ids: list[str] | None = None,
) -> list[GoldenPrompt]:
    if not prompt_ids:
        return prompts
    by_id = {prompt.id: prompt for prompt in prompts}
    missing = [prompt_id for prompt_id in prompt_ids if prompt_id not in by_id]
    if missing:
        raise ValueError(f"Unknown golden prompt id: {', '.join(missing)}")
    return [by_id[prompt_id] for prompt_id in prompt_ids]


def validate_golden_response(
    prompt: GoldenPrompt,
    response: dict[str, Any] | None,
    status_code: int | None = 200,
) -> GoldenProbeResult:
    errors: list[str] = []
    if status_code != 200:
        errors.append(f"http_status_not_200:{status_code}")
    if not isinstance(response, dict):
        errors.append("response_not_object")
        return GoldenProbeResult(
            id=prompt.id,
            prompt=prompt.prompt,
            passed=False,
            expected_domain=prompt.expected_domain,
            errors=errors,
            status_code=status_code,
        )

    if response.get("status") != "draft":
        errors.append(f"status_not_draft:{response.get('status')}")
    if response.get("saveable") is not True:
        errors.append("saveable_not_true")
    if response.get("safety_status") != prompt.expected_safety_status:
        errors.append(f"safety_status_mismatch:{response.get('safety_status')}")

    rule = response.get("rule")
    if not isinstance(rule, dict):
        errors.append("missing_rule")
        return _result_from_response(prompt, response, status_code, errors)
    if rule.get("match_type") != "ALL":
        errors.append(f"match_type_not_all:{rule.get('match_type')}")

    conditions = rule.get("conditions")
    if not isinstance(conditions, list):
        errors.append("conditions_not_array")
        conditions = []
    actual_domain = _actual_domain(conditions)
    sender_ok = actual_domain == prompt.expected_domain.lower()
    if not sender_ok:
        errors.append(f"missing_expected_domain:{prompt.expected_domain}")

    content_conditions = [
        condition
        for condition in conditions
        if isinstance(condition, dict)
        and condition.get("field") in {"subject", "body"}
        and condition.get("operator") == "contains"
        and str(condition.get("value", "")).strip()
    ]
    if not content_conditions:
        errors.append("missing_content_condition")
    expected_keywords = [keyword.lower() for keyword in prompt.expected_keywords_any]
    content_values = [str(condition.get("value", "")).lower() for condition in content_conditions]
    if expected_keywords and not any(
        keyword in value
        for keyword in expected_keywords
        for value in content_values
    ):
        errors.append("missing_expected_keyword")

    actions = rule.get("actions")
    if not isinstance(actions, list):
        errors.append("actions_not_array")
        actions = []
    if len(actions) != 1:
        errors.append(f"actions_count_not_one:{len(actions)}")
    action = actions[0] if actions and isinstance(actions[0], dict) else {}
    action_type = action.get("action_type")
    if action_type in BLOCKED_ACTIONS:
        errors.append(f"blocked_action:{action_type}")
    if action_type != prompt.expected_action_type:
        errors.append(f"action_type_mismatch:{action_type}")
    if action.get("target") != prompt.expected_target:
        errors.append(f"target_mismatch:{action.get('target')}")
    if action.get("stop_processing") is not False:
        errors.append("stop_processing_not_false")
    for candidate in actions:
        if isinstance(candidate, dict) and candidate.get("action_type") in BLOCKED_ACTIONS:
            errors.append(f"blocked_action_present:{candidate.get('action_type')}")

    warnings = response.get("warnings")
    if isinstance(warnings, list) and warnings:
        lowered = " ".join(str(warning).lower() for warning in warnings)
        if not any(token in lowered for token in ("draft", "does not mutate", "no rule was saved")):
            errors.append("warnings_missing_non_mutating_language")

    return _result_from_response(prompt, response, status_code, errors)


def run_golden_probe(
    prompts: list[GoldenPrompt],
    draft_fn: Callable[[GoldenPrompt], dict[str, Any]],
    fail_fast: bool = False,
) -> dict[str, Any]:
    results: list[GoldenProbeResult] = []
    for prompt in prompts:
        try:
            response = draft_fn(prompt)
            result = validate_golden_response(prompt, response, 200)
        except Exception as exc:
            result = GoldenProbeResult(
                id=prompt.id,
                prompt=prompt.prompt,
                passed=False,
                expected_domain=prompt.expected_domain,
                errors=[f"request_failed:{sanitize_error(exc)}"],
            )
        results.append(result)
        if fail_fast and not result.passed:
            break
    return summarize_probe_results(results, total=len(prompts))


def summarize_probe_results(
    results: list[GoldenProbeResult],
    total: int | None = None,
) -> dict[str, Any]:
    total_count = len(results) if total is None else total
    passed = sum(1 for result in results if result.passed)
    failed = len(results) - passed
    skipped = max(total_count - len(results), 0)
    return {
        "status": "passed" if failed == 0 and skipped == 0 else "failed",
        "summary": {
            "total": total_count,
            "passed": passed,
            "failed": failed,
            "skipped": skipped,
        },
        "results": [result.to_dict() for result in results],
    }


def disabled_probe_response(
    prompts: list[GoldenPrompt],
    rule_ai: dict[str, Any],
) -> dict[str, Any]:
    return {
        "status": "disabled",
        "summary": {
            "total": len(prompts),
            "passed": 0,
            "failed": 0,
            "skipped": len(prompts),
        },
        "rule_ai": rule_ai,
        "results": [],
        "warnings": [
            "Local Rule AI is disabled. Enable [mail.rule_ai].enabled=true only when intentionally testing."
        ],
        "safety": safety_flags(),
    }


def safety_flags() -> dict[str, bool]:
    return {
        "saved_rules": False,
        "sent_imessage": False,
        "mutated_gmail": False,
        "mutated_imap": False,
    }


def sanitize_error(exc: Exception) -> str:
    text = str(exc).replace("\n", " ").replace("\r", " ").strip()
    return text[:240] or exc.__class__.__name__


def _result_from_response(
    prompt: GoldenPrompt,
    response: dict[str, Any],
    status_code: int | None,
    errors: list[str],
) -> GoldenProbeResult:
    rule = response.get("rule") if isinstance(response.get("rule"), dict) else {}
    actions = rule.get("actions") if isinstance(rule.get("actions"), list) else []
    action = actions[0] if actions and isinstance(actions[0], dict) else {}
    conditions = rule.get("conditions") if isinstance(rule.get("conditions"), list) else []
    warnings = response.get("warnings") if isinstance(response.get("warnings"), list) else []
    return GoldenProbeResult(
        id=prompt.id,
        prompt=prompt.prompt,
        passed=not errors,
        expected_domain=prompt.expected_domain,
        actual_domain=_actual_domain(conditions),
        safety_status=response.get("safety_status"),
        saveable=response.get("saveable"),
        action_type=action.get("action_type"),
        target=action.get("target"),
        errors=errors,
        warnings=[str(warning) for warning in warnings],
        response_status=response.get("status"),
        status_code=status_code,
    )


def _actual_domain(conditions: list[Any]) -> str | None:
    for condition in conditions:
        if (
            isinstance(condition, dict)
            and condition.get("field") == "from_domain"
            and condition.get("operator") == "contains"
        ):
            value = str(condition.get("value", "")).strip().lower()
            if value:
                return value
    return None
