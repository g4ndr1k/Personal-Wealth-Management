#!/usr/bin/env python3
"""Manual smoke probe for local Rule AI golden prompts.

This operator-run script calls only POST /api/mail/rules/ai/draft. It never
calls the Save Rule endpoint and never mutates mailbox or rule-table state.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from agent.app.rule_ai_golden_probe import (  # noqa: E402
    DEFAULT_GOLDEN_FIXTURE,
    DRAFT_PATH,
    SAVE_RULE_PATH,
    GoldenPrompt,
    GoldenProbeResult,
    load_golden_prompts,
    run_golden_probe,
    sanitize_error,
    select_golden_prompts,
)


DEFAULT_API_BASE = "http://127.0.0.1:8090"


def run_http_probe(
    prompts: list[GoldenPrompt],
    api_base: str,
    api_key: str,
    timeout: float,
    fail_fast: bool = False,
) -> dict[str, Any]:
    url = api_base.rstrip("/") + DRAFT_PATH

    def draft_fn(prompt: GoldenPrompt) -> dict[str, Any]:
        status_code, payload = _post_json(
            url,
            {"request_text": prompt.prompt, "mode": prompt.mode},
            api_key,
            timeout,
        )
        if status_code != 200:
            return {
                "status": "http_error",
                "saveable": False,
                "safety_status": f"http_status_{status_code}",
                "rule": None,
                "warnings": [payload.get("detail") or payload.get("error") or "HTTP request failed"],
            }
        return payload

    return run_golden_probe(prompts, draft_fn=draft_fn, fail_fast=fail_fast)


def _post_json(
    url: str,
    payload: dict[str, Any],
    api_key: str,
    timeout: float,
) -> tuple[int, dict[str, Any]]:
    if not url.endswith(DRAFT_PATH):
        raise ValueError("Probe may only call the AI draft endpoint")
    if url.endswith(SAVE_RULE_PATH):
        raise ValueError("Probe must never call the Save Rule endpoint")
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "X-Api-Key": api_key,
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            response_body = response.read().decode("utf-8")
            return response.status, json.loads(response_body)
    except urllib.error.HTTPError as exc:
        response_body = exc.read().decode("utf-8", errors="replace")
        try:
            payload = json.loads(response_body)
        except json.JSONDecodeError:
            payload = {"error": response_body[:240]}
        return exc.code, payload


def print_text_report(summary: dict[str, Any]) -> None:
    for result in summary["results"]:
        if result["passed"]:
            print(f"PASS {result['id']} -> {result['expected_domain']}")
        else:
            reason = ", ".join(result.get("errors") or ["unknown_error"])
            print(f"FAIL {result['id']} -> {result['expected_domain']} ({reason})")
    counts = summary["summary"]
    print(f"{counts['passed']} passed, {counts['failed']} failed")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run local Rule AI golden prompts through the draft endpoint only."
    )
    parser.add_argument("--api-base", default=DEFAULT_API_BASE)
    parser.add_argument("--fixture", default=str(DEFAULT_GOLDEN_FIXTURE.relative_to(REPO)))
    parser.add_argument("--timeout", type=float, default=120)
    parser.add_argument("--json", action="store_true", dest="json_output")
    parser.add_argument("--prompt-id")
    parser.add_argument("--fail-fast", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    api_key = os.environ.get("FINANCE_API_KEY", "").strip()
    if not api_key:
        print("FINANCE_API_KEY is required in the environment.", file=sys.stderr)
        return 2
    try:
        prompts = select_golden_prompts(
            load_golden_prompts(args.fixture),
            [args.prompt_id] if args.prompt_id else None,
        )
        summary = run_http_probe(
            prompts,
            api_base=args.api_base,
            api_key=api_key,
            timeout=args.timeout,
            fail_fast=args.fail_fast,
        )
    except Exception as exc:
        failed = {
            "status": "failed",
            "summary": {"total": 0, "passed": 0, "failed": 1, "skipped": 0},
            "results": [
                GoldenProbeResult(
                    id="probe_error",
                    prompt="",
                    passed=False,
                    expected_domain="",
                    errors=[sanitize_error(exc)],
                ).to_dict()
            ],
        }
        if args.json_output:
            print(json.dumps(failed, indent=2, sort_keys=True))
        else:
            print_text_report(failed)
        return 1
    if args.json_output:
        print(json.dumps(summary, indent=2, sort_keys=True))
    else:
        print_text_report(summary)
    return 0 if summary["summary"]["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
