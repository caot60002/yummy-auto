# -*- coding: utf-8 -*-
"""
Helper module for startup cookie prefill and private server distribution.
Reconstructed for WebRB v2.
"""

from types import SimpleNamespace


def apply_private_server_lines(valid_cookies, ps_lines):
    """
    Distributes private server links/codes across valid cookies.
    """
    if not valid_cookies or not ps_lines:
        return 0
    assigned = 0
    clean_ps = [p.strip() for p in ps_lines if p and p.strip()]
    if not clean_ps:
        return 0
    for idx, cookie_entry in enumerate(valid_cookies):
        ps = clean_ps[idx % len(clean_ps)]
        if isinstance(cookie_entry, dict):
            cookie_entry["private_server"] = ps
            assigned += 1
        elif isinstance(cookie_entry, list) and len(cookie_entry) > 0:
            cookie_entry.append(ps)
            assigned += 1
    return assigned


def build_prefill_status(current_valid_count, total_tab, ready_sent):
    """
    Evaluates whether enough valid cookies have been prefilled to signal startup ready.
    """
    should_signal = (current_valid_count >= total_tab) and not ready_sent
    return SimpleNamespace(
        current_valid_count=current_valid_count,
        total_tab=total_tab,
        ready_sent=ready_sent,
        should_signal_ready=should_signal,
    )


def publish_valid_cookie_snapshot(
    valid_cookies_trimmed,
    cookie_positions,
    switched_usernames,
    banned_snapshot,
    total_tab,
    ps_lines,
):
    """
    Constructs and publishes a runtime snapshot of valid cookies and their status.
    """
    snapshot = {
        "valid_cookies_trimmed": list(valid_cookies_trimmed) if valid_cookies_trimmed else [],
        "cookie_positions": dict(cookie_positions) if cookie_positions else {},
        "switched_usernames": list(switched_usernames) if switched_usernames else [],
        "banned_snapshot": dict(banned_snapshot) if banned_snapshot else {},
        "total_tab": total_tab,
        "ps_lines": list(ps_lines) if ps_lines else [],
    }
    return snapshot
