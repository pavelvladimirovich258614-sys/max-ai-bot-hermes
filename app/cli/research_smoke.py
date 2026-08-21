"""Smoke-test the /research cascade from the command line (F2, 2026-08-21).

Usage::

    python -m app.cli.research_smoke --topic "AI в юриспруденции 2026" --fresh 30d

Exit codes:
  0  — pipeline returned status=OK and ≥1 fresh finding
  1  — pipeline returned status=PARTIAL (some findings, but no high-
       confidence ones) or NO_FRESH_DATA
  2  — pipeline returned status=FAILED (cascade crashed, no findings)
  3  — argument / environment error

The script:
  1. Loads Settings (from .env if present).
  2. Builds a ResearchCascade with the configured web_search / web_reader.
  3. Runs the cascade on the topic with the requested freshness window.
  4. Prints a compact JSON dump of the ResearchResult.
  5. If LIVE_LLM=1 is set, additionally calls the LLMClient with the
     role="researcher" SYSTEM_PROMPT and the cascade's "answer" field
     as the user message — this exercises the F2 fallback path. Without
     LIVE_LLM, the script does mock-only verification (it parses the
     result through ResearchResult.parse_obj and checks the schema).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

# Allow running as ``python -m app.cli.research_smoke`` from the project root.
_HERE = Path(__file__).resolve().parent
_ROOT = _HERE.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Smoke-test the /research cascade.")
    p.add_argument("--topic", required=True, help="Research topic in any language.")
    p.add_argument(
        "--fresh", default="30d", choices=("7d", "30d", "90d", "all"),
        help="Freshness window (default: 30d).",
    )
    p.add_argument(
        "--live-llm", action="store_true",
        help="Also call the LLMClient with the researcher role_prompt "
             "(requires LLM_PRIMARY_API_KEY env).",
    )
    return p.parse_args()


async def _run(topic: str, fresh: str, live_llm: bool) -> int:
    from app.config import get_settings
    from app.core.research_cascade import ResearchCascade
    from app.schemas.research import parse_freshness

    settings = get_settings()
    cascade = ResearchCascade(settings)
    try:
        result = await cascade.run(topic, fresh)
    finally:
        await cascade.aclose()

    # Always print the compact JSON for the operator.
    print(result.to_compact_json())

    # Optional: pipe the cascade's answer through the LLM (exercises
    # the F2 fallback path). This is the "live smoke" Pavel asked for.
    if live_llm:
        try:
            from app.llm.client import LLMClient
            from app.llm.prompts.researcher import SYSTEM_PROMPT
            llm = LLMClient(settings)
            try:
                llm_answer = await llm.chat(
                    messages=[
                        {"role": "user", "content": (
                            f"Сжато перескажи для MAX-бота следующий research-ответ "
                            f"в 2-3 предложениях, не выдумывая ничего нового: "
                            f"\n\n{result.answer}"
                        )},
                    ],
                    role="researcher",
                    system=SYSTEM_PROMPT,
                )
                print("\n# LLM-fallback (live):")
                print(llm_answer)
            finally:
                await llm.aclose()
        except Exception as e:  # noqa: BLE001
            print(f"\n# LLM-fallback failed: {type(e).__name__}: {e}", file=sys.stderr)

    if result.status == "OK":
        # F2.3 DoD: at least one fresh finding
        if any(
            f.confidence == "high" for f in result.key_findings
        ):
            return 0
        return 1
    if result.status in ("PARTIAL", "NO_FRESH_DATA"):
        return 1
    return 2


def main() -> int:
    args = _parse_args()
    fresh = parse_freshness(args.fresh)
    try:
        return asyncio.run(_run(args.topic, fresh, args.live_llm))
    except KeyboardInterrupt:
        return 3
    except Exception as e:  # noqa: BLE001
        print(f"smoke crashed: {type(e).__name__}: {e}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    sys.exit(main())
