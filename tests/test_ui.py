"""
Root test_ui.py — executes omni-review-intel/tests/test_ui.py tests from root workspace.
"""

from __future__ import annotations
import sys
from pathlib import Path

root_dir = Path(__file__).resolve().parent.parent
pkg_dir = root_dir / "omni-review-intel"
pkg_tests_dir = pkg_dir / "tests"

for p in (str(pkg_dir), str(pkg_tests_dir)):
    if p not in sys.path:
        sys.path.insert(0, p)

from test_ui import (
    test_ui_theme_tokens_and_css_contrast,
    test_ui_initial_render_light_mode_no_sample_demo,
    test_ui_empty_url_warning,
    test_ui_renders_state_and_switches_light_dark_mode,
    test_ui_clear_reset,
    test_ui_session_state_persistence,
    test_ui_renders_over_1000_comments_without_truncation,
    test_ui_blocks_unsafe_private_and_local_urls,
    test_ui_renders_zero_comment_state_and_escapes_html,
    test_scoped_settings_isolate_public_visitor_secrets,
    test_ui_helpers,
    app_test,
)

__all__ = [
    "test_ui_theme_tokens_and_css_contrast",
    "test_ui_initial_render_light_mode_no_sample_demo",
    "test_ui_empty_url_warning",
    "test_ui_renders_state_and_switches_light_dark_mode",
    "test_ui_clear_reset",
    "test_ui_session_state_persistence",
    "test_ui_renders_over_1000_comments_without_truncation",
    "test_ui_blocks_unsafe_private_and_local_urls",
    "test_ui_renders_zero_comment_state_and_escapes_html",
    "test_scoped_settings_isolate_public_visitor_secrets",
    "test_ui_helpers",
    "app_test",
]
