"""Test for BUG #1 fix in app.cli.research_smoke.

The bug: ``main()`` called ``parse_freshness(args.fresh)`` but the
import was only present inside ``_run()`` (line 59), so calling
``main()`` directly raised ``NameError: name 'parse_freshness' is not
defined``.

Fix: import ``parse_freshness`` at module top level so ``main()`` can
use it without going through ``_run()`` first.
"""
from __future__ import annotations

import app.cli.research_smoke as smoke


def test_research_smoke_imports_parse_freshness_at_module_level():
    """``parse_freshness`` must be accessible as a module-level attribute."""
    assert hasattr(smoke, "parse_freshness"), (
        "parse_freshness must be importable from app.cli.research_smoke at module level"
    )
    # Sanity: it must be callable, not just present.
    assert callable(smoke.parse_freshness)
    assert smoke.parse_freshness("30d") == "30d"
    assert smoke.parse_freshness("7d") == "7d"
    assert smoke.parse_freshness("all") == "all"
