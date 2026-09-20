#!/usr/bin/env python3
"""BACnet Test HTML Report Generator.

Generates self-contained, interactive HTML reports with summary metrics,
visual progress indicators, real-time search, status filtering, and
responsive tables for both Read and Write tests.
"""

from __future__ import annotations

import argparse
import html
import json
import sys
from pathlib import Path
from typing import Any


def format_val(val: Any, max_len: int = 60) -> str:
    """Format any JSON-compatible value to a clean string with html escaping."""
    if val is None:
        return '<span class="text-muted">null</span>'
    if isinstance(val, bool):
        badge_cls = "badge-bool-true" if val else "badge-bool-false"
        return f'<span class="badge {badge_cls}">{str(val)}</span>'
    if isinstance(val, (dict, list)):
        raw_s = json.dumps(val, ensure_ascii=False)
        escaped = html.escape(raw_s)
        if len(raw_s) > max_len:
            truncated = html.escape(raw_s[:max_len] + "...")
            return f'<span class="code-val truncate" title="{escaped}">{truncated}</span>'
        return f'<span class="code-val">{escaped}</span>'
    raw_s = str(val)
    escaped = html.escape(raw_s)
    if len(raw_s) > max_len:
        truncated = html.escape(raw_s[:max_len] + "...")
        return f'<span class="truncate" title="{escaped}">{truncated}</span>'
    return f"<span>{escaped}</span>"


def render_html(data: dict[str, Any], report_type: str | None = None) -> str:
    """Render data dictionary to a modern standalone HTML report string."""
    # Determine report type: 'write' if writable keys exist, else 'read'
    if report_type is None:
        if "writable" in data or any("original_value" in r for r in data.get("results", [])):
            report_type = "write"
        else:
            report_type = "read"

    is_write = report_type == "write"
    results = data.get("results", [])
    target = data.get("target_address", "Unknown Target")
    timestamp = data.get("timestamp_utc", "")
    total = len(results)

    if is_write:
        title = "BACnet Property Write & Readback Test Report"
        passed_count = sum(r.get("status") == "writable" for r in results)
        failed_count = sum(r.get("status") in ("mismatch", "write_failed", "readback_failed") for r in results)
        readonly_count = sum(r.get("status") == "read_only" for r in results)
        notsupp_count = sum(r.get("status") == "not_supported" for r in results)
    else:
        title = "BACnet Property Read Verification Report"
        passed_count = sum(r.get("status") == "passed" for r in results)
        failed_count = sum(r.get("status") == "failed" for r in results)
        readonly_count = 0
        notsupp_count = sum(r.get("status") == "not_supported" for r in results)

    passed_pct = round((passed_count / total * 100), 1) if total > 0 else 0
    failed_pct = round((failed_count / total * 100), 1) if total > 0 else 0
    readonly_pct = round((readonly_count / total * 100), 1) if total > 0 else 0
    notsupp_pct = round((notsupp_count / total * 100), 1) if total > 0 else 0

    # Build unique profiles for filter dropdown
    profiles = sorted({r.get("profile", "") for r in results if r.get("profile")})

    # Generate table rows
    rows_html: list[str] = []
    for idx, r in enumerate(results, 1):
        status = str(r.get("status", "unknown")).lower()
        obj_id = html.escape(str(r.get("object_id", "")))
        obj_name = html.escape(str(r.get("object_name", obj_id)))
        profile = html.escape(str(r.get("profile", "")).upper())
        prop = str(r.get("property", ""))
        array_idx = r.get("array_index")
        prop_display = html.escape(f"{prop}[{array_idx}]" if array_idx is not None else prop)

        # Status badge classes
        if status in ("writable", "passed"):
            badge_cls = "badge-success"
            badge_txt = "WRITABLE" if is_write else "PASSED"
        elif status == "read_only":
            badge_cls = "badge-warning"
            badge_txt = "READ ONLY"
        elif status == "not_supported":
            badge_cls = "badge-muted"
            badge_txt = "NOT SUPPORTED"
        elif status == "mismatch":
            badge_cls = "badge-danger"
            badge_txt = "MISMATCH"
        else:
            badge_cls = "badge-danger"
            badge_txt = "FAILED"

        status_badge = f'<span class="badge {badge_cls}">{badge_txt}</span>'

        if is_write:
            orig_val = format_val(r.get("original_value"))
            test_val = format_val(r.get("test_value"))
            act_val = format_val(r.get("actual_readback"))

            restored = r.get("restored")
            if restored is True:
                restore_badge = '<span class="badge badge-success-outline" title="Original value restored">Restored</span>'
            elif restored is False:
                restore_badge = '<span class="badge badge-danger" title="Failed to restore original value">Restore Failed</span>'
            else:
                restore_badge = '<span class="text-muted">-</span>'

            # Details / Notes
            notes = []
            if r.get("out_of_service_used"):
                notes.append('<span class="tag-badge">OOS toggled</span>')
            if r.get("error"):
                notes.append(f'<span class="text-danger" title="{html.escape(str(r["error"]))}">{html.escape(str(r["error"]))}</span>')
            elif r.get("message"):
                notes.append(f'<span class="text-muted">{html.escape(str(r["message"]))}</span>')
            details_html = " ".join(notes) if notes else '<span class="text-muted">-</span>'

            row = f"""<tr data-status="{status}" data-profile="{profile.lower()}">
  <td class="col-idx">{idx}</td>
  <td>{status_badge}</td>
  <td class="font-mono">{obj_id}</td>
  <td>{obj_name}</td>
  <td><span class="badge-profile">{profile}</span></td>
  <td class="font-mono font-bold">{prop_display}</td>
  <td>{orig_val}</td>
  <td>{test_val}</td>
  <td>{act_val}</td>
  <td class="text-center">{restore_badge}</td>
  <td class="col-details">{details_html}</td>
</tr>"""
        else:
            act_val = format_val(r.get("actual"))
            err = r.get("error")
            err_html = f'<span class="text-danger" title="{html.escape(str(err))}">{html.escape(str(err))}</span>' if err else '<span class="text-muted">-</span>'

            row = f"""<tr data-status="{status}" data-profile="{profile.lower()}">
  <td class="col-idx">{idx}</td>
  <td>{status_badge}</td>
  <td class="font-mono">{obj_id}</td>
  <td>{obj_name}</td>
  <td><span class="badge-profile">{profile}</span></td>
  <td class="font-mono font-bold">{prop_display}</td>
  <td class="col-value">{act_val}</td>
  <td class="col-details">{err_html}</td>
</tr>"""
        rows_html.append(row)

    table_rows = "\n".join(rows_html)

    # Extra columns for write vs read
    if is_write:
        thead_html = """<tr>
  <th style="width: 50px;">#</th>
  <th style="width: 120px;">Status</th>
  <th style="width: 160px;">Object ID</th>
  <th style="width: 160px;">Object Name</th>
  <th style="width: 80px;">Profile</th>
  <th style="width: 150px;">Property</th>
  <th>Original Val</th>
  <th>Test Val</th>
  <th>Readback Val</th>
  <th style="width: 100px; text-align: center;">Restored</th>
  <th>Details / Note</th>
</tr>"""
    else:
        thead_html = """<tr>
  <th style="width: 50px;">#</th>
  <th style="width: 120px;">Status</th>
  <th style="width: 160px;">Object ID</th>
  <th style="width: 180px;">Object Name</th>
  <th style="width: 80px;">Profile</th>
  <th style="width: 180px;">Property</th>
  <th>Read Value</th>
  <th>Error Details</th>
</tr>"""

    profile_options = "\n".join(f'<option value="{p.lower()}">{p.upper()}</option>' for p in profiles)

    status_filter_buttons = [
        f'<button class="filter-btn active" data-filter="all">All <span class="count">({total})</span></button>',
    ]
    if is_write:
        status_filter_buttons.append(
            f'<button class="filter-btn btn-success" data-filter="writable">Writable <span class="count">({passed_count})</span></button>'
        )
        if readonly_count > 0:
            status_filter_buttons.append(
                f'<button class="filter-btn btn-warning" data-filter="read_only">Read-Only <span class="count">({readonly_count})</span></button>'
            )
        if failed_count > 0:
            status_filter_buttons.append(
                f'<button class="filter-btn btn-danger" data-filter="failed">Failed/Mismatch <span class="count">({failed_count})</span></button>'
            )
        if notsupp_count > 0:
            status_filter_buttons.append(
                f'<button class="filter-btn btn-muted" data-filter="not_supported">Not Supported <span class="count">({notsupp_count})</span></button>'
            )
    else:
        status_filter_buttons.append(
            f'<button class="filter-btn btn-success" data-filter="passed">Passed <span class="count">({passed_count})</span></button>'
        )
        if failed_count > 0:
            status_filter_buttons.append(
                f'<button class="filter-btn btn-danger" data-filter="failed">Failed <span class="count">({failed_count})</span></button>'
            )

    filter_buttons_html = "\n".join(status_filter_buttons)

    cards_html = f"""
    <div class="card">
      <div class="card-label">Total Properties</div>
      <div class="card-value">{total:,}</div>
      <div class="card-sub">Tested against target</div>
    </div>
    <div class="card card-success">
      <div class="card-label">{"Writable & Verified" if is_write else "Passed"}</div>
      <div class="card-value">{passed_count:,}</div>
      <div class="card-sub">{passed_pct}% success rate</div>
    </div>
    """

    if is_write and readonly_count > 0:
        cards_html += f"""
    <div class="card card-warning">
      <div class="card-label">Read Only</div>
      <div class="card-value">{readonly_count:,}</div>
      <div class="card-sub">{readonly_pct}% of total</div>
    </div>
    """

    if failed_count > 0:
        cards_html += f"""
    <div class="card card-danger">
      <div class="card-label">{"Failed / Mismatch" if is_write else "Failed"}</div>
      <div class="card-value">{failed_count:,}</div>
      <div class="card-sub">{failed_pct}% require attention</div>
    </div>
    """
    else:
        cards_html += f"""
    <div class="card card-neutral">
      <div class="card-label">Failed</div>
      <div class="card-value">0</div>
      <div class="card-sub">All verified cleanly</div>
    </div>
    """

    if notsupp_count > 0:
        cards_html += f"""
    <div class="card card-muted">
      <div class="card-label">Not Supported</div>
      <div class="card-value">{notsupp_count:,}</div>
      <div class="card-sub">{notsupp_pct}% omitted</div>
    </div>
    """

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{title} - {html.escape(target)}</title>
  <style>
    :root {{
      --bg-primary: #0f172a;
      --bg-secondary: #1e293b;
      --bg-tertiary: #334155;
      --bg-card: #1e293b;
      --text-primary: #f8fafc;
      --text-secondary: #94a3b8;
      --text-muted: #64748b;
      --border-color: #334155;
      --accent-blue: #38bdf8;
      --accent-green: #10b981;
      --accent-red: #ef4444;
      --accent-yellow: #f59e0b;
      --accent-purple: #a855f7;
      --hover-row: #24344d;
    }}

    * {{
      box-sizing: border-box;
      margin: 0;
      padding: 0;
    }}

    body {{
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
      background-color: var(--bg-primary);
      color: var(--text-primary);
      line-height: 1.5;
      padding: 24px;
      font-size: 14px;
    }}

    .container {{
      max-width: 1440px;
      margin: 0 auto;
    }}

    /* Header */
    header {{
      background: linear-gradient(135deg, #1e293b 0%, #0f172a 100%);
      border: 1px solid var(--border-color);
      border-radius: 12px;
      padding: 24px 28px;
      margin-bottom: 24px;
      display: flex;
      flex-wrap: wrap;
      justify-content: space-between;
      align-items: center;
      gap: 16px;
      box-shadow: 0 4px 20px rgba(0, 0, 0, 0.25);
    }}

    .header-left h1 {{
      font-size: 22px;
      font-weight: 700;
      letter-spacing: -0.02em;
      color: #ffffff;
      display: flex;
      align-items: center;
      gap: 10px;
    }}

    .header-left h1 .brand-dot {{
      width: 10px;
      height: 10px;
      border-radius: 50%;
      background: var(--accent-blue);
      box-shadow: 0 0 10px var(--accent-blue);
    }}

    .header-meta {{
      display: flex;
      flex-wrap: wrap;
      gap: 12px;
      margin-top: 10px;
    }}

    .meta-tag {{
      display: inline-flex;
      align-items: center;
      gap: 6px;
      background: rgba(255, 255, 255, 0.05);
      border: 1px solid rgba(255, 255, 255, 0.1);
      padding: 4px 10px;
      border-radius: 6px;
      font-size: 12px;
      color: var(--text-secondary);
    }}

    .meta-tag strong {{
      color: var(--text-primary);
    }}

    /* Summary KPI Cards */
    .kpi-grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
      gap: 16px;
      margin-bottom: 24px;
    }}

    .card {{
      background: var(--bg-card);
      border: 1px solid var(--border-color);
      border-radius: 10px;
      padding: 18px 20px;
      position: relative;
      overflow: hidden;
      box-shadow: 0 2px 10px rgba(0, 0, 0, 0.15);
    }}

    .card::before {{
      content: "";
      position: absolute;
      top: 0;
      left: 0;
      width: 4px;
      height: 100%;
      background: var(--border-color);
    }}

    .card-success::before {{ background: var(--accent-green); }}
    .card-danger::before {{ background: var(--accent-red); }}
    .card-warning::before {{ background: var(--accent-yellow); }}
    .card-neutral::before {{ background: var(--accent-blue); }}
    .card-muted::before {{ background: var(--text-muted); }}

    .card-label {{
      font-size: 12px;
      font-weight: 600;
      text-transform: uppercase;
      letter-spacing: 0.05em;
      color: var(--text-secondary);
      margin-bottom: 6px;
    }}

    .card-value {{
      font-size: 28px;
      font-weight: 700;
      color: #ffffff;
      line-height: 1.2;
    }}

    .card-sub {{
      font-size: 12px;
      color: var(--text-muted);
      margin-top: 4px;
    }}

    /* Stacked Progress Bar */
    .progress-bar-container {{
      background: var(--bg-secondary);
      border: 1px solid var(--border-color);
      border-radius: 8px;
      padding: 12px 16px;
      margin-bottom: 24px;
    }}

    .progress-track {{
      display: flex;
      height: 10px;
      border-radius: 5px;
      overflow: hidden;
      background: rgba(255, 255, 255, 0.05);
      margin-bottom: 8px;
    }}

    .progress-segment {{
      height: 100%;
      transition: width 0.3s ease;
    }}

    .seg-passed {{ background: var(--accent-green); }}
    .seg-readonly {{ background: var(--accent-yellow); }}
    .seg-failed {{ background: var(--accent-red); }}
    .seg-muted {{ background: var(--text-muted); }}

    .progress-legend {{
      display: flex;
      flex-wrap: wrap;
      gap: 16px;
      font-size: 12px;
      color: var(--text-secondary);
    }}

    .legend-item {{
      display: flex;
      align-items: center;
      gap: 6px;
    }}

    .legend-dot {{
      width: 8px;
      height: 8px;
      border-radius: 50%;
    }}

    /* Filter & Search Toolbar */
    .toolbar {{
      background: var(--bg-secondary);
      border: 1px solid var(--border-color);
      border-radius: 10px;
      padding: 14px 18px;
      margin-bottom: 16px;
      display: flex;
      flex-wrap: wrap;
      gap: 12px;
      align-items: center;
      justify-content: space-between;
    }}

    .toolbar-left {{
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      align-items: center;
    }}

    .filter-btn {{
      background: transparent;
      border: 1px solid var(--border-color);
      color: var(--text-secondary);
      padding: 6px 12px;
      border-radius: 6px;
      cursor: pointer;
      font-size: 13px;
      font-weight: 500;
      transition: all 0.2s ease;
    }}

    .filter-btn:hover {{
      background: var(--bg-tertiary);
      color: var(--text-primary);
    }}

    .filter-btn.active {{
      background: var(--accent-blue);
      border-color: var(--accent-blue);
      color: #0f172a;
      font-weight: 600;
    }}

    .filter-btn.btn-success.active {{
      background: var(--accent-green);
      border-color: var(--accent-green);
      color: #ffffff;
    }}

    .filter-btn.btn-warning.active {{
      background: var(--accent-yellow);
      border-color: var(--accent-yellow);
      color: #0f172a;
    }}

    .filter-btn.btn-danger.active {{
      background: var(--accent-red);
      border-color: var(--accent-red);
      color: #ffffff;
    }}

    .filter-btn.btn-muted.active {{
      background: var(--text-muted);
      border-color: var(--text-muted);
      color: #ffffff;
    }}

    .toolbar-right {{
      display: flex;
      flex-wrap: wrap;
      gap: 10px;
      align-items: center;
    }}

    .search-input {{
      background: var(--bg-primary);
      border: 1px solid var(--border-color);
      border-radius: 6px;
      color: var(--text-primary);
      padding: 6px 12px;
      font-size: 13px;
      width: 240px;
      outline: none;
      transition: border-color 0.2s;
    }}

    .search-input:focus {{
      border-color: var(--accent-blue);
    }}

    .select-profile {{
      background: var(--bg-primary);
      border: 1px solid var(--border-color);
      border-radius: 6px;
      color: var(--text-primary);
      padding: 6px 10px;
      font-size: 13px;
      outline: none;
      cursor: pointer;
    }}

    /* Table */
    .table-container {{
      background: var(--bg-card);
      border: 1px solid var(--border-color);
      border-radius: 10px;
      overflow: hidden;
      box-shadow: 0 4px 20px rgba(0, 0, 0, 0.15);
    }}

    .table-wrapper {{
      max-height: 75vh;
      overflow-y: auto;
    }}

    table {{
      width: 100%;
      border-collapse: collapse;
      text-align: left;
      font-size: 13px;
    }}

    th {{
      background: #182234;
      color: var(--text-secondary);
      font-weight: 600;
      font-size: 12px;
      text-transform: uppercase;
      letter-spacing: 0.05em;
      padding: 12px 14px;
      border-bottom: 1px solid var(--border-color);
      position: sticky;
      top: 0;
      z-index: 5;
    }}

    td {{
      padding: 10px 14px;
      border-bottom: 1px solid rgba(51, 65, 85, 0.5);
      color: var(--text-primary);
      vertical-align: middle;
    }}

    tr:hover {{
      background: var(--hover-row);
    }}

    /* Badges */
    .badge {{
      display: inline-block;
      padding: 3px 8px;
      border-radius: 4px;
      font-size: 11px;
      font-weight: 600;
      letter-spacing: 0.02em;
    }}

    .badge-success {{
      background: rgba(16, 185, 129, 0.2);
      color: #34d399;
      border: 1px solid rgba(16, 185, 129, 0.4);
    }}

    .badge-success-outline {{
      background: transparent;
      color: #34d399;
      border: 1px solid rgba(16, 185, 129, 0.4);
    }}

    .badge-danger {{
      background: rgba(239, 68, 68, 0.2);
      color: #f87171;
      border: 1px solid rgba(239, 68, 68, 0.4);
    }}

    .badge-warning {{
      background: rgba(245, 158, 11, 0.2);
      color: #fbbf24;
      border: 1px solid rgba(245, 158, 11, 0.4);
    }}

    .badge-muted {{
      background: rgba(100, 116, 139, 0.2);
      color: #94a3b8;
      border: 1px solid rgba(100, 116, 139, 0.3);
    }}

    .badge-profile {{
      display: inline-block;
      background: rgba(56, 189, 248, 0.15);
      color: #38bdf8;
      border: 1px solid rgba(56, 189, 248, 0.3);
      padding: 2px 6px;
      border-radius: 4px;
      font-size: 11px;
      font-weight: 600;
    }}

    .badge-bool-true {{
      background: rgba(16, 185, 129, 0.15);
      color: #34d399;
      padding: 2px 6px;
      border-radius: 3px;
    }}

    .badge-bool-false {{
      background: rgba(239, 68, 68, 0.15);
      color: #f87171;
      padding: 2px 6px;
      border-radius: 3px;
    }}

    .tag-badge {{
      display: inline-block;
      background: rgba(168, 85, 247, 0.15);
      color: #c084fc;
      border: 1px solid rgba(168, 85, 247, 0.3);
      padding: 2px 6px;
      border-radius: 4px;
      font-size: 11px;
    }}

    /* Utility */
    .font-mono {{ font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace; font-size: 12px; }}
    .font-bold {{ font-weight: 600; color: #f1f5f9; }}
    .text-muted {{ color: var(--text-muted); }}
    .text-danger {{ color: #f87171; }}
    .text-center {{ text-align: center; }}

    .col-idx {{ color: var(--text-muted); font-size: 11px; }}
    .col-value, .col-details {{ max-width: 320px; word-break: break-word; }}
    .code-val {{ font-family: ui-monospace, monospace; font-size: 12px; }}

    .truncate {{
      display: inline-block;
      max-width: 260px;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
      vertical-align: bottom;
    }}

    /* Table Footer / Counter */
    .table-footer {{
      padding: 10px 18px;
      background: #182234;
      border-top: 1px solid var(--border-color);
      display: flex;
      justify-content: space-between;
      align-items: center;
      font-size: 12px;
      color: var(--text-secondary);
    }}

    /* Empty state */
    .empty-row {{
      text-align: center;
      padding: 40px;
      color: var(--text-muted);
    }}
  </style>
</head>
<body>
  <div class="container">
    <!-- Header -->
    <header>
      <div class="header-left">
        <h1><span class="brand-dot"></span> {title}</h1>
        <div class="header-meta">
          <span class="meta-tag">Target: <strong>{html.escape(target)}</strong></span>
          <span class="meta-tag">Generated: <strong>{html.escape(timestamp)}</strong></span>
          <span class="meta-tag">Total Items: <strong>{total:,}</strong></span>
        </div>
      </div>
    </header>

    <!-- Summary KPI Cards -->
    <div class="kpi-grid">
      {cards_html}
    </div>

    <!-- Progress Bar -->
    <div class="progress-bar-container">
      <div class="progress-track">
        <div class="progress-segment seg-passed" style="width: {passed_pct}%;" title="Passed/Writable: {passed_pct}%"></div>
        <div class="progress-segment seg-readonly" style="width: {readonly_pct}%;" title="Read Only: {readonly_pct}%"></div>
        <div class="progress-segment seg-failed" style="width: {failed_pct}%;" title="Failed: {failed_pct}%"></div>
        <div class="progress-segment seg-muted" style="width: {notsupp_pct}%;" title="Not Supported: {notsupp_pct}%"></div>
      </div>
      <div class="progress-legend">
        <div class="legend-item"><span class="legend-dot seg-passed"></span> {"Writable" if is_write else "Passed"} ({passed_pct}%)</div>
        {"<div class='legend-item'><span class='legend-dot seg-readonly'></span> Read Only (" + str(readonly_pct) + "%)</div>" if is_write and readonly_pct > 0 else ""}
        {"<div class='legend-item'><span class='legend-dot seg-failed'></span> Failed/Mismatch (" + str(failed_pct) + "%)</div>" if failed_pct > 0 else ""}
        {"<div class='legend-item'><span class='legend-dot seg-muted'></span> Not Supported (" + str(notsupp_pct) + "%)</div>" if notsupp_pct > 0 else ""}
      </div>
    </div>

    <!-- Toolbar -->
    <div class="toolbar">
      <div class="toolbar-left">
        {filter_buttons_html}
      </div>
      <div class="toolbar-right">
        <select id="profileFilter" class="select-profile">
          <option value="all">All Profiles</option>
          {profile_options}
        </select>
        <input type="text" id="searchInput" class="search-input" placeholder="Search objects, props, values...">
      </div>
    </div>

    <!-- Table -->
    <div class="table-container">
      <div class="table-wrapper">
        <table id="reportTable">
          <thead>
            {thead_html}
          </thead>
          <tbody id="tableBody">
            {table_rows}
          </tbody>
        </table>
      </div>
      <div class="table-footer">
        <span id="counterText">Showing {total:,} of {total:,} entries</span>
        <span class="text-muted">BACnet Regression Automation Suite</span>
      </div>
    </div>
  </div>

  <script>
    (function() {{
      const searchInput = document.getElementById('searchInput');
      const profileFilter = document.getElementById('profileFilter');
      const filterBtns = document.querySelectorAll('.filter-btn');
      const tableBody = document.getElementById('tableBody');
      const rows = Array.from(tableBody.querySelectorAll('tr'));
      const counterText = document.getElementById('counterText');

      let currentStatusFilter = 'all';
      let currentProfileFilter = 'all';
      let currentQuery = '';

      function applyFilter() {{
        let visibleCount = 0;
        const q = currentQuery.toLowerCase().trim();

        rows.forEach(row => {{
          const status = row.getAttribute('data-status') || '';
          const profile = row.getAttribute('data-profile') || '';
          const text = row.textContent.toLowerCase();

          // Check status filter
          let matchesStatus = false;
          if (currentStatusFilter === 'all') {{
            matchesStatus = true;
          }} else if (currentStatusFilter === 'writable') {{
            matchesStatus = (status === 'writable');
          }} else if (currentStatusFilter === 'passed') {{
            matchesStatus = (status === 'passed');
          }} else if (currentStatusFilter === 'read_only') {{
            matchesStatus = (status === 'read_only');
          }} else if (currentStatusFilter === 'not_supported') {{
            matchesStatus = (status === 'not_supported');
          }} else if (currentStatusFilter === 'failed') {{
            matchesStatus = (status === 'failed' || status === 'mismatch' || status === 'write_failed' || status === 'readback_failed');
          }}

          // Check profile filter
          let matchesProfile = (currentProfileFilter === 'all' || profile === currentProfileFilter);

          // Check text search
          let matchesSearch = !q || text.includes(q);

          if (matchesStatus && matchesProfile && matchesSearch) {{
            row.style.display = '';
            visibleCount++;
          }} else {{
            row.style.display = 'none';
          }}
        }});

        counterText.textContent = `Showing ${{visibleCount.toLocaleString()}} of ${{rows.length.toLocaleString()}} entries`;
      }}

      // Status buttons
      filterBtns.forEach(btn => {{
        btn.addEventListener('click', () => {{
          filterBtns.forEach(b => b.classList.remove('active'));
          btn.classList.add('active');
          currentStatusFilter = btn.getAttribute('data-filter');
          applyFilter();
        }});
      }});

      // Profile select
      profileFilter.addEventListener('change', (e) => {{
        currentProfileFilter = e.target.value.toLowerCase();
        applyFilter();
      }});

      // Search debounce
      let debounceTimer = null;
      searchInput.addEventListener('input', (e) => {{
        clearTimeout(debounceTimer);
        debounceTimer = setTimeout(() => {{
          currentQuery = e.target.value;
          applyFilter();
        }}, 120);
      }});
    }})();
  </script>
</body>
</html>
"""


def generate_html_report(
    data: dict[str, Any],
    output_path: Path | str,
    report_type: str | None = None,
) -> Path:
    """Generate and write an HTML report file from result dict."""
    p = Path(output_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    html_content = render_html(data, report_type=report_type)
    p.write_text(html_content, encoding="utf-8")
    return p


def main() -> int:
    parser = argparse.ArgumentParser(description="Convert BACnet test JSON report to an HTML table report")
    parser.add_argument("json_file", help="Path to input JSON report file")
    parser.add_argument("--output", "-o", default=None, help="Path to output HTML report (default: same name with .html)")
    parser.add_argument("--type", "-t", choices=["read", "write"], default=None, help="Force report type (read or write)")
    args = parser.parse_args()

    json_path = Path(args.json_file)
    if not json_path.exists():
        print(f"Error: JSON file not found: {json_path}", file=sys.stderr)
        return 1

    try:
        data = json.loads(json_path.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"Error: Failed to parse JSON file {json_path}: {e}", file=sys.stderr)
        return 1

    out_path = Path(args.output) if args.output else json_path.with_suffix(".html")
    generate_html_report(data, out_path, report_type=args.type)
    print(f"HTML Report successfully generated: {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
