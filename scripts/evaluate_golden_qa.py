from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.routing import (  # noqa: E402
    DetailRequestStrategy,
    ExplicitGroupBySQLStrategy,
    ExplicitPythonStrategy,
    HybridStructuredSemanticStrategy,
    LLMRouterStrategy,
    MultiColumnCountStrategy,
    MultiRouteStrategy,
    PythonCalculationStrategy,
    QueryRouter,
    SQLRouteStrategy,
    SemanticSearchStrategy,
    SimpleCountStrategy,
    StatusIdStrategy,
)
from app.logging_config import log  # noqa: E402


DEFAULT_GOLDEN_PATH = REPO_ROOT / "private" / "golden_qa.json"


@dataclass
class EvaluationResult:
    id: str
    expected_route: str
    actual_route: str
    matched: bool
    reason: str
    source: str
    candidates: list[str]


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if not args.show_router_logs:
        log.disabled = True
    payload = load_payload(args.golden_set)
    results = evaluate_routes(payload, use_llm_router=args.use_llm_router)
    summary = summarize(results)
    report = {
        "golden_set": str(args.golden_set),
        "mode": "full-router" if args.use_llm_router else "deterministic-router",
        "summary": summary,
        "results": [asdict(result) for result in results],
    }

    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")

    print_summary(report, verbose=args.verbose)
    return 0 if summary["accuracy"] >= args.min_accuracy else 1


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Evaluate TalkToMyExcel routing against a private or synthetic golden Q/A set."
    )
    parser.add_argument(
        "golden_set",
        nargs="?",
        type=Path,
        default=DEFAULT_GOLDEN_PATH,
        help="Path to golden_qa.json. Defaults to private/golden_qa.json.",
    )
    parser.add_argument(
        "--use-llm-router",
        action="store_true",
        help="Use the production router including LLM fallback. Default avoids LLM/network-dependent routing.",
    )
    parser.add_argument(
        "--min-accuracy",
        type=float,
        default=1.0,
        help="Exit with status 1 when route accuracy is below this value.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="Optional JSON report path. Use a private/ path when evaluating production-derived data.",
    )
    parser.add_argument("--verbose", action="store_true", help="Print per-question route results.")
    parser.add_argument("--show-router-logs", action="store_true", help="Keep application router logs on stdout.")
    return parser.parse_args(argv)


def load_payload(path: Path) -> dict:
    if not path.exists():
        raise FileNotFoundError(
            f"Golden set not found: {path}. Generate or pass a path, for example private/golden_qa.json."
        )
    payload = json.loads(path.read_text(encoding="utf-8"))
    if "questions" not in payload or not isinstance(payload["questions"], list):
        raise ValueError("Golden set must contain a questions list")
    return payload


def evaluate_routes(payload: dict, *, use_llm_router: bool = False) -> list[EvaluationResult]:
    router = build_router(use_llm_router=use_llm_router)
    metadata = metadata_from_profile(payload.get("profile") or {})
    results = []
    for item in payload["questions"]:
        plan = router.plan(item["question"], metadata)
        expected = str(item.get("expected_route") or "").strip()
        actual = plan.route
        results.append(
            EvaluationResult(
                id=str(item.get("id") or ""),
                expected_route=expected,
                actual_route=actual,
                matched=actual == expected,
                reason=plan.reason,
                source=plan.source,
                candidates=list(plan.ordered_routes()),
            )
        )
    return results


def build_router(*, use_llm_router: bool) -> QueryRouter:
    strategies = [
        ExplicitPythonStrategy(),
        StatusIdStrategy(),
        MultiRouteStrategy(),
        HybridStructuredSemanticStrategy(),
        ExplicitGroupBySQLStrategy(),
        SemanticSearchStrategy(),
        DetailRequestStrategy(),
        PythonCalculationStrategy(),
        MultiColumnCountStrategy(),
        SimpleCountStrategy(),
        SQLRouteStrategy(),
    ]
    if use_llm_router:
        strategies.append(LLMRouterStrategy())
    return QueryRouter(strategies=strategies)


def metadata_from_profile(profile: dict) -> dict:
    tables = []
    for index, sheet in enumerate(profile.get("sheets") or [], start=1):
        detected = sheet.get("detected") or {}
        semantic_columns = [column for column in detected.get("semantic") or [] if column]
        tables.append(
            {
                "filename": profile.get("source_file"),
                "sheet": sheet.get("name") or f"Sheet{index}",
                "table": f"golden_sheet_{index}",
                "rows": sheet.get("rows", 0),
                "columns": sheet.get("columns", []),
                "semantic_columns": semantic_columns,
            }
        )
    return {"datasets": [], "tables": tables}


def summarize(results: list[EvaluationResult]) -> dict:
    total = len(results)
    matched = sum(1 for result in results if result.matched)
    by_expected: dict[str, dict[str, int]] = {}
    for result in results:
        bucket = by_expected.setdefault(result.expected_route, {"total": 0, "matched": 0})
        bucket["total"] += 1
        bucket["matched"] += int(result.matched)
    return {
        "total": total,
        "matched": matched,
        "failed": total - matched,
        "accuracy": matched / total if total else 0.0,
        "by_expected_route": by_expected,
    }


def print_summary(report: dict, *, verbose: bool) -> None:
    summary = report["summary"]
    print(
        json.dumps(
            {
                "mode": report["mode"],
                "total": summary["total"],
                "matched": summary["matched"],
                "failed": summary["failed"],
                "accuracy": round(summary["accuracy"], 4),
                "by_expected_route": summary["by_expected_route"],
            },
            ensure_ascii=False,
        )
    )
    if not verbose:
        return
    for result in report["results"]:
        status = "ok" if result["matched"] else "fail"
        print(
            json.dumps(
                {
                    "id": result["id"],
                    "status": status,
                    "expected": result["expected_route"],
                    "actual": result["actual_route"],
                    "reason": result["reason"],
                    "source": result["source"],
                    "candidates": result["candidates"],
                },
                ensure_ascii=False,
            )
        )


if __name__ == "__main__":
    raise SystemExit(main())
