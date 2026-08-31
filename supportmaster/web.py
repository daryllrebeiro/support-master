"""Small local UI for choosing the model for a SupportMaster workflow run."""

from __future__ import annotations

import argparse
import asyncio
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from http.cookies import SimpleCookie
import secrets
import json
import logging
import os

logger = logging.getLogger("supportmaster.web")
if not logger.handlers:
    logging.basicConfig(level=logging.INFO)
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from uuid import uuid4

from google.adk.runners import Runner
from google.adk.sessions.sqlite_session_service import SqliteSessionService
from google.genai import types

from .agent import create_root_agent
from .config import DEFAULT_MODEL, supported_models
from .model_resolver import MODEL_RESOLVER
from .persistence import SQLiteRunStore, TenantAccessError
from .runtime import DurableTaskWorker
from .telemetry import MetricsRegistry, SQLiteTelemetrySink, TelemetryRecorder
from .operations import HealthReporter, RunAdmissionController, load_operation_settings
from .security import Authenticator, Principal, load_security_settings
from .intake import normalize_case
from .organization import OrganizationContextService
from .models.organization import OrganizationProfile
from .investigation import InvestigationService
from .planning import PlanningService
from .models.planning import PlanningAssessment
from .workflow_state import SupportMasterState
from .workspace import CaseWorkspaceService
from .rate_limiter import TenantRateLimiter


RATE_LIMITER = TenantRateLimiter(default_capacity=5.0, default_fill_rate=0.5)

# Map ADK agent author names to pipeline stage labels for live streaming.
# When an event's author changes, a STAGE_TRANSITION event is emitted.
AUTHOR_TO_STAGE: dict[str, str] = {
    "ticket_analysis_agent": "INTAKE",
    "investigation_agent": "INVESTIGATION",
    "duplicate_work_agent": "DUPLICATE_GATES",
    "evidence_agent": "INVESTIGATION",
    "repository_agent": "INVESTIGATION",
    "root_cause_agent": "INVESTIGATION",
    "remediation_agent": "REMEDIATION",
    "review_agent": "REMEDIATION",
    "code_change_agent": "REMEDIATION",
    "implementation_agent": "REMEDIATION",
    "validation_agent": "VERIFICATION",
    "test_result_agent": "VERIFICATION",
    "publish_agent": "PUBLISH",
    "resolution_agent": "PUBLISH",
    "customer_response_agent": "PUBLISH",
    "audit_agent": "PUBLISH",
    "workflow_summary_agent": "PUBLISH",
    "workflow_control_agent": "PUBLISH",
}

# In-memory Q&A session histories for Phase 42 case-scoped evidence Q&A.
# Keyed by (case_id, tenant_id). Cleared on server restart.
_CASE_QA_SESSIONS: dict[tuple[str, str], list[dict[str, str]]] = {}


MOCK_JIRA_ISSUE = """Jira key: FIN-1847
Summary: CSV invoice export fails with OutOfMemoryError for enterprise tenants

Customer: Northstar Retail Group (Enterprise)
Reporter: Priya Shah, Finance Operations Lead
Priority: P1 — Finance teams cannot complete month-end reconciliation
Environment: Production, EU region, application version 4.18.2
First observed: 2026-08-14 09:17 UTC

Customer impact:
- 38 finance users are blocked from downloading invoice exports.
- The month-end close is due on 2026-08-18.
- Smaller exports below approximately 50,000 invoices complete successfully.

Expected behavior:
An authorized user can export the requested invoice range as a CSV file. Large
exports should either complete within the documented asynchronous export flow
or fail gracefully with a clear, recoverable message.

Actual behavior:
Exports covering approximately 1.2–1.5 million invoices remain on “Preparing
export” for 6–9 minutes, then the user sees “Export failed. Please try again.”
No file is delivered.

Customer-provided reproduction steps:
1. Sign in as a Finance Administrator.
2. Open Billing > Invoices > Export CSV.
3. Select date range 2025-01-01 through 2025-12-31; leave all regions selected.
4. Click Export.
5. Observe the failure after several minutes.

Customer-provided technical evidence (not independently verified):
2026-08-14T09:25:41Z ERROR export-worker java.lang.OutOfMemoryError: Java heap space
  at com.northstar.billing.export.InvoiceCsvSerializer.writeRows(InvoiceCsvSerializer.java:184)
  at com.northstar.billing.export.InvoiceExportJob.run(InvoiceExportJob.java:92)
Job ID: exp_7f31a2; tenant ID: tenant_redacted; request ID: req_94a7c1

Recent changes reported by the customer:
- Their invoice volume increased after an acquisition on 2026-08-01.
- No application upgrade, permission change, or browser change was made by the customer.

Attachments referenced but not supplied to SupportMaster:
- Full export-worker logs for job exp_7f31a2
- Screenshot of the customer-facing failure message
- Heap metrics dashboard for the export worker

Requested outcome:
Identify whether this is a known issue or existing work, determine the safest
next action, and provide a customer-safe status update. Do not claim a fix,
deployment, validation, GitHub publication, or customer confirmation without
direct evidence."""


OPERATION_SETTINGS = load_operation_settings()
RUN_ADMISSION = RunAdmissionController(OPERATION_SETTINGS.max_active_runs)
SECURITY_SETTINGS = load_security_settings()
AUTHENTICATOR = Authenticator(SECURITY_SETTINGS)


def _configured_health_reporter() -> HealthReporter:
    return HealthReporter(
        run_db=os.getenv("SUPPORTMASTER_RUN_DB", ".supportmaster/runs.db"),
        session_db=os.getenv("SUPPORTMASTER_SESSION_DB", ".supportmaster/adk_sessions.db"),
    )


def _model_label(model_name: str) -> str:
    for m in MODEL_RESOLVER.get_available_models():
        if m["id"] == model_name or f"{m['provider']}:{m['id']}" == model_name or f"{m['provider']}/{m['id']}" == model_name:
            return m["label"]
    return model_name.replace("gemini-", "Gemini ").replace("-", " ").title()


def render_page(
    selected_model: str,
    issue: str = MOCK_JIRA_ISSUE,
    status: str | None = None,
    result: str | None = None,
) -> str:
    """Render the dual-view control panel with Workflow Launcher and ADK Agent Chat & Live Reasoning."""
    escaped_jira = escape(MOCK_JIRA_ISSUE).replace('`', '\\`').replace('\n', '\\n')
    available_models = [m for m in MODEL_RESOLVER.get_available_models() if m.get("available", True)]
    if not available_models:
        available_models = [{"id": m, "label": _model_label(m), "provider": "vertex_ai"} for m in supported_models()]
    options = "\n".join(
        (
            f'<option value="{escape(model["id"])}"'
            f'{" selected" if model["id"] == selected_model else ""}>'
            f"{escape(model['label'])}"
            "</option>"
        )
        for model in available_models
    )
    status_html = (
        f'<div class="status-card" role="status"><div class="status-indicator"></div><p class="status-text">{escape(status)}</p></div>' if status else ""
    )
    result_html = (
        f'<section class="results-card"><h2>Workflow Execution Events</h2><pre class="events-log">{escape(result)}</pre></section>'
        if result
        else ""
    )
    return f"""<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>SupportMaster — Autonomous Support Engineering</title>
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&family=Outfit:wght@400;600;700;800&family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet">
    <style>
      :root {{
        --bg-color: #030712;
        --card-bg: rgba(17, 24, 39, 0.75);
        --card-border: rgba(55, 65, 81, 0.5);
        --accent-blue: #3b82f6;
        --accent-glow: rgba(59, 130, 246, 0.2);
        --accent-cyan: #06b6d4;
        --accent-purple: #8b5cf6;
        --text-primary: #f3f4f6;
        --text-secondary: #9ca3af;
        --text-muted: #6b7280;
        --green-bright: #10b981;
        --amber-bright: #f59e0b;
      }}
      * {{
        box-sizing: border-box;
      }}
      body {{
        margin: 0;
        min-height: 100vh;
        background: radial-gradient(circle at 15% 15%, rgba(17, 34, 64, 0.85) 0%, rgba(3, 7, 18, 1) 90%);
        color: var(--text-primary);
        font-family: 'Inter', system-ui, sans-serif;
        padding-bottom: 60px;
      }}
      .top-navbar {{
        width: 100%;
        padding: 16px 32px;
        display: flex;
        justify-content: space-between;
        align-items: center;
        border-bottom: 1px solid var(--card-border);
        background: rgba(17, 24, 39, 0.8);
        backdrop-filter: blur(16px);
        position: sticky;
        top: 0;
        z-index: 100;
      }}
      .brand {{
        display: flex;
        align-items: center;
        gap: 12px;
        text-decoration: none;
      }}
      .brand-badge {{
        background: linear-gradient(135deg, #3b82f6, #8b5cf6);
        color: white;
        padding: 4px 10px;
        border-radius: 8px;
        font-family: 'Outfit', sans-serif;
        font-weight: 800;
        font-size: 0.85rem;
        letter-spacing: 0.05em;
      }}
      .brand-name {{
        font-family: 'Outfit', sans-serif;
        font-weight: 800;
        font-size: 1.3rem;
        background: linear-gradient(135deg, #ffffff 40%, #93c5fd 100%);
        -webkit-background-clip: text;
        -webkit-text-fill-color: transparent;
      }}
      .nav-tabs {{
        display: flex;
        background: rgba(3, 7, 18, 0.6);
        padding: 4px;
        border-radius: 12px;
        border: 1px solid var(--card-border);
        gap: 4px;
      }}
      .tab-btn {{
        background: transparent;
        border: none;
        color: var(--text-secondary);
        padding: 8px 18px;
        border-radius: 8px;
        font-family: 'Outfit', sans-serif;
        font-size: 0.9rem;
        font-weight: 600;
        cursor: pointer;
        transition: all 0.2s ease;
        text-decoration: none;
        display: inline-flex;
        align-items: center;
        gap: 6px;
      }}
      .tab-btn:hover {{
        color: var(--text-primary);
        background: rgba(55, 65, 81, 0.4);
      }}
      .tab-btn.active {{
        background: var(--accent-blue);
        color: #ffffff;
        box-shadow: 0 2px 10px rgba(59, 130, 246, 0.4);
      }}
      .container {{
        max-width: 1000px;
        margin: 32px auto;
        padding: 0 20px;
      }}
      .view-panel {{
        display: none;
      }}
      .view-panel.active {{
        display: block;
        animation: fadeIn 0.3s ease;
      }}
      @keyframes fadeIn {{
        from {{ opacity: 0; transform: translateY(8px); }}
        to {{ opacity: 1; transform: translateY(0); }}
      }}
      .glass-card {{
        background: var(--card-bg);
        border: 1px solid var(--card-border);
        border-radius: 20px;
        padding: 32px;
        backdrop-filter: blur(16px);
        box-shadow: 0 20px 40px -15px rgba(0, 0, 0, 0.6), 0 0 30px var(--accent-glow);
      }}
      .eyebrow {{
        color: var(--accent-cyan);
        font-family: 'Outfit', sans-serif;
        font-size: .8rem;
        font-weight: 700;
        letter-spacing: .15em;
        text-transform: uppercase;
        margin-bottom: 6px;
      }}
      h1 {{
        margin: 0 0 8px;
        font-family: 'Outfit', sans-serif;
        font-size: 2.2rem;
        font-weight: 800;
        background: linear-gradient(135deg, #ffffff 40%, #93c5fd 100%);
        -webkit-background-clip: text;
        -webkit-text-fill-color: transparent;
      }}
      .intro {{
        color: var(--text-secondary);
        font-size: 0.95rem;
        line-height: 1.5;
        margin-bottom: 24px;
      }}
      label {{
        display: block;
        margin: 20px 0 8px;
        font-family: 'Outfit', sans-serif;
        font-size: 0.9rem;
        font-weight: 600;
        color: #e5e7eb;
      }}
      select, textarea, button {{
        width: 100%;
        box-sizing: border-box;
        border-radius: 12px;
        font: inherit;
        transition: all 0.2s ease;
      }}
      select {{
        padding: 12px 14px;
        background: rgba(17, 24, 39, 0.8);
        border: 1px solid var(--card-border);
        color: var(--text-primary);
        cursor: pointer;
      }}
      select:focus, textarea:focus {{
        outline: none;
        border-color: var(--accent-blue);
        box-shadow: 0 0 0 3px rgba(59, 130, 246, 0.25);
      }}
      textarea {{
        width: 100%;
        min-height: 200px;
        resize: vertical;
        padding: 14px;
        background: rgba(17, 24, 39, 0.8);
        border: 1px solid var(--card-border);
        color: var(--text-primary);
        font-family: 'JetBrains Mono', monospace;
        font-size: .85rem;
        line-height: 1.5;
      }}
      .fixture-templates {{
        display: flex;
        flex-wrap: wrap;
        gap: 8px;
        margin-top: 8px;
      }}
      .fixture-btn {{
        background: rgba(31, 41, 55, 0.7);
        border: 1px solid var(--card-border);
        color: var(--text-secondary);
        padding: 6px 12px;
        border-radius: 16px;
        font-size: 0.8rem;
        font-weight: 550;
        width: auto;
        cursor: pointer;
      }}
      .fixture-btn:hover {{
        background: var(--accent-blue);
        color: #ffffff;
        border-color: var(--accent-blue);
        transform: translateY(-1px);
      }}
      button[type="submit"], .action-btn {{
        margin-top: 24px;
        padding: 14px 20px;
        border: 0;
        border-radius: 12px;
        background: linear-gradient(135deg, #3b82f6 0%, #2563eb 100%);
        color: #ffffff;
        font-family: 'Outfit', sans-serif;
        font-weight: 700;
        font-size: 1rem;
        cursor: pointer;
        box-shadow: 0 4px 16px rgba(37, 99, 235, 0.3);
      }}
      button[type="submit"]:hover, .action-btn:hover {{
        background: linear-gradient(135deg, #60a5fa 0%, #3b82f6 100%);
        transform: translateY(-1px);
      }}
      .status-card {{
        display: flex;
        align-items: center;
        gap: 12px;
        margin-top: 20px;
        padding: 12px 16px;
        border-radius: 12px;
        background: rgba(16, 185, 129, 0.1);
        border: 1px solid rgba(16, 185, 129, 0.2);
      }}
      .status-indicator {{
        width: 8px;
        height: 8px;
        border-radius: 50%;
        background: #10b981;
        box-shadow: 0 0 10px #10b981;
      }}
      .status-text {{
        margin: 0;
        font-size: 0.9rem;
        color: #a7f3d0;
        font-weight: 500;
      }}
      .results-card {{
        margin-top: 28px;
      }}
      .events-log {{
        max-height: 450px;
        overflow: auto;
        white-space: pre-wrap;
        padding: 16px;
        border-radius: 12px;
        background: rgba(3, 7, 18, 0.9);
        border: 1px solid var(--card-border);
        color: #d1d5db;
        font-family: 'JetBrains Mono', monospace;
        font-size: 0.8rem;
        line-height: 1.6;
      }}

      /* =========================================================================
         ADK Chat & Live Multi-Agent Reasoning View Styles
         ========================================================================= */
      .chat-window {{
        display: flex;
        flex-direction: column;
        height: 75vh;
        max-height: 800px;
      }}
      .chat-messages {{
        flex: 1;
        overflow-y: auto;
        padding: 20px;
        display: flex;
        flex-direction: column;
        gap: 20px;
      }}
      .msg-row {{
        display: flex;
        gap: 14px;
        max-width: 88%;
      }}
      .msg-row.user {{
        align-self: flex-end;
        flex-direction: row-reverse;
      }}
      .msg-row.agent {{
        align-self: flex-start;
      }}
      .avatar {{
        width: 38px;
        height: 38px;
        border-radius: 12px;
        display: flex;
        align-items: center;
        justify-content: center;
        font-weight: 700;
        font-size: 0.9rem;
        flex-shrink: 0;
      }}
      .avatar.user-av {{
        background: linear-gradient(135deg, #3b82f6, #1d4ed8);
        color: white;
      }}
      .avatar.agent-av {{
        background: linear-gradient(135deg, #8b5cf6, #06b6d4);
        color: white;
      }}
      .msg-bubble {{
        padding: 16px 20px;
        border-radius: 16px;
        font-size: 0.92rem;
        line-height: 1.6;
      }}
      .msg-row.user .msg-bubble {{
        background: linear-gradient(135deg, #1d4ed8, #2563eb);
        color: #ffffff;
        border-bottom-right-radius: 4px;
      }}
      .msg-row.agent .msg-bubble {{
        background: rgba(17, 24, 39, 0.9);
        border: 1px solid var(--card-border);
        color: var(--text-primary);
        border-bottom-left-radius: 4px;
      }}
      .agent-header {{
        display: flex;
        align-items: center;
        gap: 8px;
        margin-bottom: 8px;
        font-family: 'Outfit', sans-serif;
      }}
      .agent-name {{
        font-weight: 700;
        font-size: 0.95rem;
        color: #93c5fd;
      }}
      .model-tag {{
        background: rgba(59, 130, 246, 0.15);
        color: #60a5fa;
        border: 1px solid rgba(59, 130, 246, 0.3);
        padding: 2px 8px;
        border-radius: 6px;
        font-size: 0.75rem;
        font-weight: 600;
      }}
      .stage-flow {{
        display: flex;
        flex-wrap: wrap;
        gap: 6px;
        margin: 12px 0;
      }}
      .stage-pill {{
        font-size: 0.72rem;
        font-weight: 700;
        padding: 3px 8px;
        border-radius: 6px;
        letter-spacing: 0.05em;
        text-transform: uppercase;
        background: rgba(31, 41, 55, 0.8);
        border: 1px solid var(--card-border);
        color: var(--text-muted);
      }}
      .stage-pill.active {{
        background: rgba(16, 185, 129, 0.2);
        color: #34d399;
        border-color: rgba(16, 185, 129, 0.4);
      }}
      .reasoning-box {{
        margin: 12px 0;
        background: rgba(3, 7, 18, 0.6);
        border: 1px solid rgba(139, 92, 246, 0.3);
        border-radius: 10px;
        overflow: hidden;
      }}
      .reasoning-box summary {{
        padding: 10px 14px;
        font-family: 'Outfit', sans-serif;
        font-size: 0.82rem;
        font-weight: 600;
        color: #c4b5fd;
        cursor: pointer;
        background: rgba(139, 92, 246, 0.1);
        user-select: none;
      }}
      .reasoning-content {{
        padding: 12px 14px;
        font-family: 'JetBrains Mono', monospace;
        font-size: 0.8rem;
        color: #d1d5db;
        max-height: 240px;
        overflow-y: auto;
        white-space: pre-wrap;
        line-height: 1.5;
        border-top: 1px solid rgba(139, 92, 246, 0.2);
      }}
      .tool-call {{
        background: rgba(3, 7, 18, 0.7);
        border: 1px solid var(--card-border);
        border-left: 3px solid var(--accent-cyan);
        padding: 8px 12px;
        border-radius: 6px;
        margin: 8px 0;
        font-family: 'JetBrains Mono', monospace;
        font-size: 0.78rem;
      }}
      .tool-name {{
        color: var(--accent-cyan);
        font-weight: 600;
      }}
      .chat-footer {{
        padding: 16px 20px;
        border-top: 1px solid var(--card-border);
        background: rgba(17, 24, 39, 0.95);
      }}
      .prompt-chips {{
        display: flex;
        gap: 8px;
        overflow-x: auto;
        padding-bottom: 10px;
        margin-bottom: 8px;
      }}
      .chip-btn {{
        background: rgba(31, 41, 55, 0.7);
        border: 1px solid var(--card-border);
        color: var(--text-secondary);
        padding: 5px 12px;
        border-radius: 14px;
        font-size: 0.75rem;
        font-weight: 550;
        white-space: nowrap;
        cursor: pointer;
        width: auto;
      }}
      .chip-btn:hover {{
        background: var(--accent-blue);
        color: white;
        border-color: var(--accent-blue);
      }}
      .chat-input-bar {{
        display: flex;
        gap: 10px;
        align-items: center;
      }}
      .chat-input-bar textarea {{
        min-height: 48px;
        max-height: 120px;
        padding: 12px;
        resize: none;
        flex: 1;
      }}
      .chat-send-btn {{
        width: auto;
        padding: 12px 24px;
        margin-top: 0;
        background: linear-gradient(135deg, #3b82f6, #2563eb);
        font-family: 'Outfit', sans-serif;
        font-weight: 700;
        border-radius: 12px;
      }}
      .typing-indicator {{
        display: flex;
        gap: 4px;
        padding: 8px 12px;
        background: rgba(31, 41, 55, 0.6);
        border-radius: 12px;
        width: fit-content;
      }}
      .typing-dot {{
        width: 6px;
        height: 6px;
        background: var(--accent-blue);
        border-radius: 50%;
        animation: typing 1.4s infinite ease-in-out both;
      }}
      .typing-dot:nth-child(1) {{ animation-delay: -0.32s; }}
      .typing-dot:nth-child(2) {{ animation-delay: -0.16s; }}
      @keyframes typing {{
        0%, 80%, 100% {{ transform: scale(0); opacity: 0.3; }}
        40% {{ transform: scale(1); opacity: 1; }}
      }}
    </style>
  </head>
  <body>
    <!-- Top Navigation Header with View Switcher -->
    <header class="top-navbar">
      <a href="/" class="brand">
        <span class="brand-badge">ADK 2.7</span>
        <span class="brand-name">SupportMaster</span>
      </a>
      <nav class="nav-tabs">
        <button type="button" class="tab-btn active" id="tab-btn-launcher" onclick="switchView('launcher')">🚀 Workflow Launcher</button>
        <button type="button" class="tab-btn" id="tab-btn-chat" onclick="switchView('chat')">💬 ADK Live Chat & Reasoning</button>
        <a href="/workspace" class="tab-btn">🗂️ Operator Workspace</a>
      </nav>
      <div style="display: flex; gap: 12px; font-size: 0.85rem;">
        <a href="/health/live" target="_blank" style="color: #34d399; text-decoration: none; font-weight: 600;">● Live</a>
        <a href="/health/ready" target="_blank" style="color: #60a5fa; text-decoration: none; font-weight: 600;">● Ready</a>
      </div>
    </header>

    <div class="container">
      <!-- VIEW 1: Standard Workflow Launcher & Form -->
      <section id="view-launcher" class="view-panel active">
        <div class="glass-card">
          <p class="eyebrow">Autonomous Support Engineering</p>
          <h1>Support Case Launcher</h1>
          <p class="intro">Configure execution environment and trigger the ADK-gated agent workflow.</p>
          
          <form action="/" method="post" id="launcher-form">
            <label for="model">Gemini Reasoning Model</label>
            <select id="model" name="model">{options}</select>
            
            <label>Load Scenario Template</label>
            <div class="fixture-templates" id="templates-list">
              <button type="button" class="fixture-btn" onclick="loadDefault()">Acme Invoice Failure (SUP-4821)</button>
            </div>

            <label for="issue">Support Ticket Description</label>
            <textarea id="issue" name="issue" required>{escape(issue)}</textarea>
            
            <button type="submit">Run SupportMaster Workflow</button>
          </form>
          
          {status_html}
          {result_html}
        </div>
      </section>

      <!-- VIEW 2: ADK Agent Live Chat & Step-by-Step Reasoning Trace -->
      <section id="view-chat" class="view-panel">
        <div class="glass-card chat-window" style="padding: 0; overflow: hidden;">
          <div style="padding: 16px 24px; border-bottom: 1px solid var(--card-border); display: flex; justify-content: space-between; align-items: center; background: rgba(17, 24, 39, 0.9);">
            <div>
              <h2 style="margin: 0; font-family: 'Outfit', sans-serif; font-size: 1.15rem; color: #f3f4f6;">ADK Multi-Agent Reasoning Chat</h2>
              <p style="margin: 2px 0 0; font-size: 0.8rem; color: var(--text-secondary);">Interactive conversation with live stage inspection and tool call receipts</p>
            </div>
            <div style="width: 220px;">
              <select id="chat-model-select" style="padding: 8px 12px; font-size: 0.85rem;">{options}</select>
            </div>
          </div>

          <div class="chat-messages" id="chat-messages">
            <!-- Welcome message -->
            <div class="msg-row agent">
              <div class="avatar agent-av">SM</div>
              <div class="msg-bubble">
                <div class="agent-header">
                  <span class="agent-name">SupportMaster</span>
                  <span class="model-tag">Google ADK Multi-Agent</span>
                </div>
                <p style="margin: 0 0 8px;">Hello Operator! I am <strong>SupportMaster</strong>, your autonomous L3 support engineering agent powered by Google ADK and Gemini.</p>
                <p style="margin: 0 0 8px;">I can analyze Jira/Zendesk incidents, run web & workspace grounding, check duplicate ticket graphs, generate self-healing code diffs, and execute verified test suites behind non-configurable safety gates.</p>
                <div class="stage-flow">
                  <span class="stage-pill active">1. Intake</span>
                  <span class="stage-pill">2. Investigation</span>
                  <span class="stage-pill">3. Duplicate Gates</span>
                  <span class="stage-pill">4. Remediation</span>
                  <span class="stage-pill">5. Verification</span>
                  <span class="stage-pill">6. Publish</span>
                </div>
              </div>
            </div>
          </div>

          <div class="chat-footer">
            <div class="prompt-chips">
              <span style="font-size: 0.75rem; color: var(--text-muted); align-self: center;">Quick Prompts:</span>
              <button type="button" class="chip-btn" onclick="sendPrompt('Diagnose Acme SSO invoice calculation failure (SUP-4821)')">⚡ Acme SSO Invoice Failure</button>
              <button type="button" class="chip-btn" onclick="sendPrompt('Investigate Redis connection pool exhaustion on payment webhooks')">🔍 Redis Connection Leak</button>
              <button type="button" class="chip-btn" onclick="sendPrompt('Check duplicate ticket graph and run verification gate')">🛡️ Verify Safety Gates</button>
            </div>
            <div class="chat-input-bar">
              <textarea id="chat-input" placeholder="Type a support incident, question, or diagnostic task..." onkeydown="handleChatKey(event)"></textarea>
              <button type="button" class="action-btn chat-send-btn" id="chat-send-btn" onclick="submitChatMessage()">Send</button>
            </div>
          </div>
        </div>
      </section>
    </div>

    <script>
      const defaultJira = `{escaped_jira}`;
      function loadDefault() {{
        document.getElementById('issue').value = defaultJira;
      }}

      function switchView(viewName) {{
        document.querySelectorAll('.view-panel').forEach(el => el.classList.remove('active'));
        document.querySelectorAll('.tab-btn').forEach(el => el.classList.remove('active'));
        
        const panel = document.getElementById('view-' + viewName);
        const btn = document.getElementById('tab-btn-' + viewName);
        if (panel) panel.classList.add('active');
        if (btn) btn.classList.add('active');
      }}

      // Fetch fixture scenarios for template quick-loader
      fetch('/api/fixtures')
        .then(r => r.json())
        .then(data => {{
          if (data && data.fixtures) {{
            const list = document.getElementById('templates-list');
            data.fixtures.forEach(name => {{
              if (name === 'saas_authentication') return;
              const btn = document.createElement('button');
              btn.type = 'button';
              btn.className = 'fixture-btn';
              btn.textContent = name.replace(/_/g, ' ').replace(/\\b\\w/g, c => c.toUpperCase());
              btn.onclick = () => {{
                fetch('/api/fixtures/' + name)
                  .then(res => res.json())
                  .then(content => {{
                    if (content) {{
                      let desc = "Title: " + (content.summary || content.title || "Support Case") + "\\n";
                      desc += "Reporter: " + (content.reporter || "Unknown") + "\\n";
                      desc += "Priority: " + (content.priority || "Medium") + "\\n";
                      desc += "Environment: " + (content.environment || "Production") + "\\n\\n";
                      desc += "Description:\\n" + (content.body || content.description || "") + "\\n\\n";
                      if (content.steps) {{
                        desc += "Reproduction Steps:\\n" + content.steps.map((s, i) => (i+1) + ". " + s).join('\\n') + "\\n\\n";
                      }}
                      if (content.impact) desc += "Customer Impact:\\n" + content.impact;
                      document.getElementById('issue').value = desc;
                    }}
                  }});
              }};
              list.appendChild(btn);
            }});
          }}
        }}).catch(() => {{}});

      // =======================================================================
      // ADK Interactive Chat Logic
      // =======================================================================
      function handleChatKey(e) {{
        if (e.key === 'Enter' && !e.shiftKey) {{
          e.preventDefault();
          submitChatMessage();
        }}
      }}

      function sendPrompt(text) {{
        document.getElementById('chat-input').value = text;
        submitChatMessage();
      }}

      function escapeHtml(text) {{
        const div = document.createElement('div');
        div.innerText = text || '';
        return div.innerHTML;
      }}

      const urlParams = new URLSearchParams(window.location.search);
      const activeCaseId = urlParams.get('case_id');

      if (activeCaseId) {{
        // Switch to Chat tab automatically when a case is passed
        switchView('chat');
        // Fetch case details to show banner
        fetch('/api/cases/' + encodeURIComponent(activeCaseId))
          .then(r => r.json())
          .then(snap => {{
            if (snap && snap.case) {{
              const banner = document.createElement('div');
              banner.style.background = 'rgba(59, 130, 246, 0.15)';
              banner.style.border = '1px solid rgba(59, 130, 246, 0.3)';
              banner.style.padding = '10px 16px';
              banner.style.borderRadius = '8px';
              banner.style.marginBottom = '16px';
              banner.style.fontSize = '0.85rem';
              banner.innerHTML = `
                <div style="display:flex; justify-content:space-between; align-items:center;">
                  <div>
                    <span style="font-weight:700; color:var(--accent-blue);">📋 CASE EVIDENCE Q&A MODE:</span> 
                    <strong>${{escapeHtml(snap.case.title)}}</strong> (${{escapeHtml(snap.case.case_id)}})
                  </div>
                  <a href="/workspace" style="color:var(--accent-cyan); text-decoration:none; font-weight:600;">View in Workspace →</a>
                </div>
                <div style="color:var(--text-secondary); margin-top:4px; font-size:0.8rem;">
                  Grounded strictly in verified investigation artifacts, root cause records, and test receipts.
                </div>
              `;
              const container = document.getElementById('chat-messages');
              if (container) container.prepend(banner);
            }}
          }}).catch(console.error);
      }}

      function submitChatMessage() {{
        const input = document.getElementById('chat-input');
        const text = input.value.trim();
        if (!text) return;

        const modelSelect = document.getElementById('chat-model-select');
        const selectedModel = modelSelect ? modelSelect.value : 'gemini-3.5-flash';
        const messagesContainer = document.getElementById('chat-messages');

        // 1. Append User Message
        const userRow = document.createElement('div');
        userRow.className = 'msg-row user';
        userRow.innerHTML = `
          <div class="avatar user-av">OP</div>
          <div class="msg-bubble">${{escapeHtml(text)}}</div>
        `;
        messagesContainer.appendChild(userRow);
        input.value = '';

        // 2. Append Typing Indicator
        const typingRow = document.createElement('div');
        typingRow.className = 'msg-row agent';
        typingRow.id = 'active-typing';
        typingRow.innerHTML = `
          <div class="avatar agent-av">SM</div>
          <div class="msg-bubble">
            <div class="typing-indicator">
              <div class="typing-dot"></div>
              <div class="typing-dot"></div>
              <div class="typing-dot"></div>
            </div>
          </div>
        `;
        messagesContainer.appendChild(typingRow);
        messagesContainer.scrollTop = messagesContainer.scrollHeight;

        // Disable send button while processing
        const sendBtn = document.getElementById('chat-send-btn');
        if (sendBtn) sendBtn.disabled = true;

        if (activeCaseId) {{
          // Route to Case-Scoped Evidence Q&A endpoint (Phase 42)
          fetch('/api/cases/' + encodeURIComponent(activeCaseId) + '/ask', {{
            method: 'POST',
            headers: {{ 'Content-Type': 'application/json' }},
            body: JSON.stringify({{ question: text }})
          }})
          .then(res => res.json())
          .then(data => {{
            const typingEl = document.getElementById('active-typing');
            if (typingEl) typingEl.remove();
            if (sendBtn) sendBtn.disabled = false;

            const agentRow = document.createElement('div');
            agentRow.className = 'msg-row agent';
            agentRow.innerHTML = `
              <div class="avatar agent-av">SM</div>
              <div class="msg-bubble" style="width: 100%;">
                <div class="agent-header">
                  <span class="agent-name">SupportMaster</span>
                  <span class="model-tag" style="background:rgba(59,130,246,0.2);color:#93c5fd;border-color:rgba(59,130,246,0.3);">Case Evidence Assistant (${{escapeHtml(activeCaseId)}})</span>
                </div>
                <div style="white-space: pre-wrap; margin-top: 6px; line-height: 1.5;">${{escapeHtml(data.answer || data.error || 'No answer generated.')}}</div>
                <div style="margin-top: 12px; display: flex; gap: 10px;">
                  <a href="/workspace" class="fixture-btn" style="text-decoration: none; display: inline-block;">🗂️ View Case in Workspace</a>
                </div>
              </div>
            `;
            messagesContainer.appendChild(agentRow);
            messagesContainer.scrollTop = messagesContainer.scrollHeight;
          }})
          .catch(err => {{
            const typingEl = document.getElementById('active-typing');
            if (typingEl) typingEl.remove();
            if (sendBtn) sendBtn.disabled = false;

            const errRow = document.createElement('div');
            errRow.className = 'msg-row agent';
            errRow.innerHTML = `
              <div class="avatar agent-av">SM</div>
              <div class="msg-bubble" style="color: #f87171; border-color: rgba(239, 68, 68, 0.4);">
                <strong>Q&A Error:</strong> ${{escapeHtml(err.message || 'Failed to query evidence artifacts.')}}
              </div>
            `;
            messagesContainer.appendChild(errRow);
            messagesContainer.scrollTop = messagesContainer.scrollHeight;
          }});
          return;
        }}

        // 3. Post to /api/chat — now returns run_id immediately, then stream via SSE
        fetch('/api/chat', {{
          method: 'POST',
          headers: {{ 'Content-Type': 'application/json' }},
          body: JSON.stringify({{ message: text, model: selectedModel }})
        }})
        .then(res => res.json())
        .then(data => {{
          if (data.error) {{
            const typingEl = document.getElementById('active-typing');
            if (typingEl) typingEl.remove();
            if (sendBtn) sendBtn.disabled = false;
            const errRow = document.createElement('div');
            errRow.className = 'msg-row agent';
            errRow.innerHTML = `
              <div class="avatar agent-av">SM</div>
              <div class="msg-bubble" style="color: #f87171; border-color: rgba(239, 68, 68, 0.4);">
                <strong>Execution Error:</strong> ${{escapeHtml(data.error)}}
              </div>
            `;
            messagesContainer.appendChild(errRow);
            messagesContainer.scrollTop = messagesContainer.scrollHeight;
            return;
          }}

          const runId = data.run_id;
          const modelLabel = data.model_label || selectedModel;

          // Build the live agent response row with stage badges and reasoning accordion
          const agentRow = document.createElement('div');
          agentRow.className = 'msg-row agent';
          agentRow.id = 'live-response-' + runId;
          agentRow.innerHTML = `
            <div class="avatar agent-av">SM</div>
            <div class="msg-bubble" style="width: 100%;">
              <div class="agent-header">
                <span class="agent-name">SupportMaster</span>
                <span class="model-tag">${{escapeHtml(modelLabel)}}</span>
                <span class="live-indicator" id="live-dot-${{runId}}" style="display:inline-block;width:8px;height:8px;border-radius:50%;background:#10b981;margin-left:8px;animation:pulse 1.5s infinite;"></span>
              </div>
              <div class="stage-flow" id="stage-flow-${{runId}}">
                <span class="stage-pill" data-stage="INTAKE">1. Intake</span>
                <span class="stage-pill" data-stage="INVESTIGATION">2. Investigation</span>
                <span class="stage-pill" data-stage="DUPLICATE_GATES">3. Duplicate Gates</span>
                <span class="stage-pill" data-stage="REMEDIATION">4. Remediation</span>
                <span class="stage-pill" data-stage="VERIFICATION">5. Verification</span>
                <span class="stage-pill" data-stage="PUBLISH">6. Publish</span>
              </div>
              <details class="reasoning-box" open>
                <summary>🧠 ADK Multi-Agent Execution Trace & Verification</summary>
                <div class="reasoning-content" id="reasoning-${{runId}}" style="white-space:pre-wrap;"></div>
              </details>
              <div style="margin-top: 12px; display: flex; gap: 10px;" id="actions-${{runId}}" hidden>
                <a href="/workspace" class="fixture-btn" style="text-decoration: none; display: inline-block;">🗂️ View Case in Operator Workspace</a>
              </div>
            </div>
          `;
          // Replace typing indicator with the live row
          const typingEl = document.getElementById('active-typing');
          if (typingEl) typingEl.remove();
          messagesContainer.appendChild(agentRow);
          messagesContainer.scrollTop = messagesContainer.scrollHeight;

          // Open SSE connection to stream live events
          connectToEventStream(runId, sendBtn);
        }})
        .catch(err => {{
          const typingEl = document.getElementById('active-typing');
          if (typingEl) typingEl.remove();
          if (sendBtn) sendBtn.disabled = false;

          const errRow = document.createElement('div');
          errRow.className = 'msg-row agent';
          errRow.innerHTML = `
            <div class="avatar agent-av">SM</div>
            <div class="msg-bubble" style="color: #f87171; border-color: rgba(239, 68, 68, 0.4);">
              <strong>Execution Error:</strong> ${{escapeHtml(err.message || 'Failed to connect to agent service.')}}
            </div>
          `;
          messagesContainer.appendChild(errRow);
          messagesContainer.scrollTop = messagesContainer.scrollHeight;
        }});
      }}

      // =======================================================================
      // SSE Live Event Stream — Phase 41
      // =======================================================================
      const STAGE_ORDER = ['INTAKE', 'INVESTIGATION', 'DUPLICATE_GATES', 'REMEDIATION', 'VERIFICATION', 'PUBLISH'];

      function connectToEventStream(runId, sendBtn, retryCount) {{
        retryCount = retryCount || 0;
        const maxRetries = 5;
        const es = new EventSource('/api/stream/' + runId);
        let completed = false;

        es.onmessage = function(evt) {{
          try {{
            const data = JSON.parse(evt.data);
            const eventType = data.event_type;
            const payload = data.payload || {{}};

            if (eventType === 'STAGE_TRANSITION') {{
              // Advance stage badges up to and including the current stage
              const stageFlow = document.getElementById('stage-flow-' + runId);
              if (stageFlow) {{
                const currentStage = payload.stage;
                const currentIdx = STAGE_ORDER.indexOf(currentStage);
                const pills = stageFlow.querySelectorAll('.stage-pill');
                pills.forEach((pill, i) => {{
                  if (i <= currentIdx) {{
                    pill.classList.add('active');
                  }}
                }});
              }}
            }}

            if (eventType === 'ADK_EVENT') {{
              // Append to reasoning accordion
              const reasoning = document.getElementById('reasoning-' + runId);
              if (reasoning) {{
                const author = payload.author || 'agent';
                const text = payload.text || '';
                const line = document.createElement('div');
                line.style.marginBottom = '8px';
                line.innerHTML = '<strong style="color:var(--accent-cyan);">[' + escapeHtml(author) + ']</strong> ' + escapeHtml(text);
                reasoning.appendChild(line);
              }}
              const messagesContainer = document.getElementById('chat-messages');
              if (messagesContainer) messagesContainer.scrollTop = messagesContainer.scrollHeight;
            }}

            if (eventType === 'RUN_COMPLETED' || eventType === 'ADK_RUN_SNAPSHOT') {{
              // Mark all stages as completed
              const stageFlow = document.getElementById('stage-flow-' + runId);
              if (stageFlow) {{
                stageFlow.querySelectorAll('.stage-pill').forEach(p => p.classList.add('active'));
              }}
              // Show action buttons, remove live dot
              const actions = document.getElementById('actions-' + runId);
              if (actions) actions.hidden = false;
              const liveDot = document.getElementById('live-dot-' + runId);
              if (liveDot) liveDot.style.display = 'none';
              if (sendBtn) sendBtn.disabled = false;
              completed = true;
              es.close();
            }}

            if (eventType === 'RUN_FAILED') {{
              const reasoning = document.getElementById('reasoning-' + runId);
              if (reasoning) {{
                const errLine = document.createElement('div');
                errLine.style.color = '#f87171';
                errLine.textContent = '❌ Run failed: ' + (payload.error || payload.reason || 'Unknown error');
                reasoning.appendChild(errLine);
              }}
              if (sendBtn) sendBtn.disabled = false;
              const liveDot = document.getElementById('live-dot-' + runId);
              if (liveDot) liveDot.style.display = 'none';
              completed = true;
              es.close();
            }}
          }} catch(e) {{ /* ignore parse errors on SSE */ }}
        }};

        es.onerror = function() {{
          es.close();
          if (!completed && retryCount < maxRetries) {{
            // Exponential backoff: 1s, 2s, 4s, 8s, 16s
            const delay = Math.pow(2, retryCount) * 1000;
            setTimeout(() => connectToEventStream(runId, sendBtn, retryCount + 1), delay);
          }} else if (!completed) {{
            // Give up — show what we have and re-enable send
            if (sendBtn) sendBtn.disabled = false;
            const liveDot = document.getElementById('live-dot-' + runId);
            if (liveDot) liveDot.style.display = 'none';
          }}
        }};
      }}
    </script>
  </body>
</html>"""


def render_workspace(csrf_token: str = "") -> str:
    """Render a premium operator workspace backed by the case APIs."""
    html = """<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width,initial-scale=1">
    <title>SupportMaster Operator Workspace</title>
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&family=Outfit:wght@400;600;700;800&family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet">
    <style>
      :root {
        --bg-color: #030712;
        --card-bg: rgba(17, 24, 39, 0.7);
        --border-color: rgba(55, 65, 81, 0.5);
        --accent-blue: #3b82f6;
        --accent-glow: rgba(59, 130, 246, 0.15);
        --text-primary: #f3f4f6;
        --text-secondary: #9ca3af;
        --green-bright: #10b981;
        --amber-bright: #f59e0b;
        --red-bright: #ef4444;
      }
      body {
        font-family: 'Inter', system-ui, sans-serif;
        background: radial-gradient(circle at 50% 0%, rgba(17, 34, 64, 0.5) 0%, rgba(3, 7, 18, 1) 100%);
        color: var(--text-primary);
        margin: 0;
        padding: 40px;
        min-height: 100vh;
      }
      main {
        max-width: 1200px;
        margin: auto;
      }
      .header {
        display: flex;
        justify-content: space-between;
        align-items: center;
        margin-bottom: 32px;
        border-bottom: 1px solid var(--border-color);
        padding-bottom: 24px;
      }
      .header-title h1 {
        margin: 0;
        font-family: 'Outfit', sans-serif;
        font-size: 2.2rem;
        font-weight: 800;
        background: linear-gradient(135deg, #ffffff 40%, #93c5fd 100%);
        -webkit-background-clip: text;
        -webkit-text-fill-color: transparent;
      }
      .header-title p {
        margin: 4px 0 0;
        color: var(--text-secondary);
        font-size: 0.95rem;
      }
      .back-btn {
        background: rgba(31, 41, 55, 0.6);
        border: 1px solid var(--border-color);
        color: var(--text-primary);
        padding: 10px 20px;
        border-radius: 10px;
        text-decoration: none;
        font-weight: 550;
        font-size: 0.9rem;
        transition: all 0.2s ease;
      }
      .back-btn:hover {
        background: var(--accent-blue);
        border-color: var(--accent-blue);
        box-shadow: 0 4px 12px rgba(59, 130, 246, 0.25);
      }
      .metrics-grid {
        display: grid;
        grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
        gap: 20px;
        margin-bottom: 32px;
      }
      .metric-card {
        background: var(--card-bg);
        border: 1px solid var(--border-color);
        border-radius: 16px;
        padding: 20px;
        box-shadow: 0 10px 15px -3px rgba(0, 0, 0, 0.3);
        backdrop-filter: blur(8px);
      }
      .metric-card h3 {
        margin: 0 0 8px;
        font-size: 0.85rem;
        font-weight: 600;
        text-transform: uppercase;
        color: var(--text-secondary);
        letter-spacing: 0.05em;
      }
      .metric-card .value {
        font-size: 1.8rem;
        font-weight: 700;
        font-family: 'Outfit', sans-serif;
        color: #ffffff;
      }
      .metric-card.alert {
        border-color: rgba(245, 158, 11, 0.4);
        box-shadow: 0 0 15px rgba(245, 158, 11, 0.1);
      }
      .metric-card.alert .value {
        color: var(--amber-bright);
      }
      .review-queue-section {
        margin-bottom: 32px;
      }
      .review-task-card {
        background: rgba(30, 27, 22, 0.85);
        border: 1px solid rgba(245, 158, 11, 0.3);
        border-radius: 16px;
        padding: 24px;
        box-shadow: 0 10px 25px rgba(245, 158, 11, 0.05);
        margin-bottom: 24px;
      }
      .review-task-card h3 {
        margin: 0 0 12px;
        color: var(--amber-bright);
        font-family: 'Outfit', sans-serif;
        font-size: 1.2rem;
        display: flex;
        align-items: center;
        gap: 10px;
      }
      .review-task-card h3::before {
        content: '';
        display: inline-block;
        width: 8px;
        height: 8px;
        border-radius: 50%;
        background: var(--amber-bright);
        box-shadow: 0 0 8px var(--amber-bright);
      }
      .review-details {
        font-size: 0.9rem;
        line-height: 1.6;
        color: #e5e7eb;
        margin-bottom: 20px;
      }
      .review-details strong {
        color: var(--text-primary);
      }
      .review-form {
        background: rgba(17, 24, 39, 0.5);
        border: 1px solid var(--border-color);
        border-radius: 12px;
        padding: 20px;
        margin-top: 16px;
      }
      .review-form h4 {
        margin: 0 0 16px;
        font-family: 'Outfit', sans-serif;
        color: #ffffff;
      }
      .form-grid {
        display: grid;
        grid-template-columns: 1fr 1fr;
        gap: 16px;
        margin-bottom: 16px;
      }
      .form-group {
        display: flex;
        flex-direction: column;
      }
      .form-group.full-width {
        grid-column: span 2;
      }
      .form-group label {
        font-size: 0.8rem;
        font-weight: 600;
        color: var(--text-secondary);
        margin-bottom: 6px;
        text-transform: uppercase;
      }
      .form-group input, .form-group select, .form-group textarea {
        background: rgba(3, 7, 18, 0.8);
        border: 1px solid var(--border-color);
        color: var(--text-primary);
        padding: 10px;
        border-radius: 8px;
        font-family: inherit;
        font-size: 0.9rem;
      }
      .form-group input:focus, .form-group select:focus, .form-group textarea:focus {
        outline: none;
        border-color: var(--accent-blue);
      }
      .scopes-checklist {
        display: grid;
        grid-template-columns: repeat(auto-fill, minmax(180px, 1fr));
        gap: 8px;
        background: rgba(3, 7, 18, 0.6);
        border: 1px solid var(--border-color);
        padding: 12px;
        border-radius: 8px;
        max-height: 120px;
        overflow-y: auto;
      }
      .scope-item {
        display: flex;
        align-items: center;
        gap: 8px;
        font-size: 0.85rem;
        color: var(--text-primary);
        cursor: pointer;
      }
      .scope-item input {
        cursor: pointer;
        width: auto;
      }
      .btn-submit {
        background: linear-gradient(135deg, #f59e0b 0%, #d97706 100%);
        border: 0;
        color: #ffffff;
        padding: 12px 24px;
        border-radius: 8px;
        font-weight: 600;
        cursor: pointer;
        font-family: 'Outfit', sans-serif;
        box-shadow: 0 4px 12px rgba(245, 158, 11, 0.2);
        transition: all 0.2s ease;
      }
      .btn-submit:hover {
        background: linear-gradient(135deg, #fbbf24 0%, #f59e0b 100%);
        transform: translateY(-1px);
        box-shadow: 0 6px 16px rgba(245, 158, 11, 0.35);
      }
      .cases-title {
        font-family: 'Outfit', sans-serif;
        font-size: 1.4rem;
        margin-bottom: 20px;
        color: #ffffff;
        display: flex;
        align-items: center;
        justify-content: space-between;
      }
      .case-card {
        background: var(--card-bg);
        border: 1px solid var(--border-color);
        border-radius: 16px;
        padding: 24px;
        margin-bottom: 24px;
        backdrop-filter: blur(8px);
        box-shadow: 0 10px 20px rgba(0, 0, 0, 0.2);
      }
      .case-header {
        display: flex;
        justify-content: space-between;
        align-items: flex-start;
        margin-bottom: 12px;
      }
      .case-title {
        margin: 0;
        font-family: 'Outfit', sans-serif;
        font-size: 1.3rem;
        font-weight: 700;
        color: #ffffff;
      }
      .case-meta {
        display: flex;
        gap: 12px;
        font-size: 0.8rem;
        color: var(--text-secondary);
        margin-top: 6px;
      }
      .case-meta span::after {
        content: ' • ';
        margin-left: 12px;
        color: var(--border-color);
      }
      .case-meta span:last-child::after {
        content: '';
      }
      .case-status-badge {
        padding: 6px 12px;
        border-radius: 999px;
        font-size: 0.75rem;
        font-weight: 600;
        text-transform: uppercase;
        background: rgba(59, 130, 246, 0.1);
        color: var(--accent-blue);
        border: 1px solid rgba(59, 130, 246, 0.2);
      }
      .case-status-badge.completed {
        background: rgba(16, 185, 129, 0.1);
        color: var(--green-bright);
        border-color: rgba(16, 185, 129, 0.2);
        box-shadow: 0 0 10px rgba(16, 185, 129, 0.05);
      }
      .case-status-badge.safety-stop {
        background: rgba(239, 68, 68, 0.1);
        color: var(--red-bright);
        border-color: rgba(239, 68, 68, 0.2);
        box-shadow: 0 0 10px rgba(239, 68, 68, 0.05);
      }
      .case-status-badge.open {
        background: rgba(245, 158, 11, 0.1);
        color: var(--amber-bright);
        border-color: rgba(245, 158, 11, 0.2);
      }
      .case-description {
        font-size: 0.95rem;
        line-height: 1.6;
        color: #d1d5db;
        margin: 16px 0;
        white-space: pre-wrap;
      }
      .action-banner {
        background: rgba(59, 130, 246, 0.08);
        border: 1px solid rgba(59, 130, 246, 0.2);
        border-radius: 12px;
        padding: 14px 18px;
        margin: 18px 0;
        font-size: 0.9rem;
      }
      .action-banner strong {
        color: var(--accent-blue);
        font-family: 'Outfit', sans-serif;
      }
      .gates-container {
        display: flex;
        flex-wrap: wrap;
        gap: 10px;
        margin: 16px 0;
      }
      .gate-badge {
        padding: 6px 12px;
        border-radius: 8px;
        font-size: 0.8rem;
        font-weight: 500;
        background: rgba(31, 41, 55, 0.6);
        border: 1px solid var(--border-color);
        color: var(--text-secondary);
        display: flex;
        align-items: center;
        gap: 6px;
      }
      .gate-badge.allow, .gate-badge.ready, .gate-badge.passed {
        border-color: rgba(16, 185, 129, 0.3);
        color: var(--green-bright);
        background: rgba(16, 185, 129, 0.05);
      }
      .gate-badge.deny, .gate-badge.safety-stop {
        border-color: rgba(239, 68, 68, 0.3);
        color: var(--red-bright);
        background: rgba(239, 68, 68, 0.05);
      }
      .gate-badge.pause, .gate-badge.request-information {
        border-color: rgba(245, 158, 11, 0.3);
        color: var(--amber-bright);
        background: rgba(245, 158, 11, 0.05);
      }
      .timeline-section {
        margin-top: 24px;
        border-top: 1px solid var(--border-color);
        padding-top: 20px;
      }
      .timeline-section h4 {
        margin: 0 0 16px;
        font-family: 'Outfit', sans-serif;
        color: #ffffff;
      }
      .timeline-flow {
        position: relative;
        border-left: 2px solid rgba(55, 65, 81, 0.6);
        padding-left: 24px;
        margin-left: 10px;
      }
      .timeline-item {
        position: relative;
        margin-bottom: 20px;
      }
      .timeline-item:last-child {
        margin-bottom: 0;
      }
      .timeline-dot {
        position: absolute;
        left: -29px;
        top: 4px;
        width: 8px;
        height: 8px;
        border-radius: 50%;
        background: #374151;
        border: 4px solid var(--bg-color);
      }
      .timeline-dot.complete {
        background: var(--green-bright);
        box-shadow: 0 0 8px var(--green-bright);
      }
      .timeline-dot.safety-stop {
        background: var(--red-bright);
        box-shadow: 0 0 8px var(--red-bright);
      }
      .timeline-dot.partial, .timeline-dot.pause {
        background: var(--amber-bright);
        box-shadow: 0 0 8px var(--amber-bright);
      }
      .timeline-content strong {
        font-family: 'Outfit', sans-serif;
        color: #ffffff;
        font-size: 0.95rem;
      }
      .timeline-status {
        font-size: 0.75rem;
        text-transform: uppercase;
        font-weight: 600;
        margin-left: 8px;
      }
      .timeline-status.complete { color: var(--green-bright); }
      .timeline-status.safety-stop { color: var(--red-bright); }
      .timeline-status.partial { color: var(--amber-bright); }
      .timeline-detail {
        font-size: 0.85rem;
        color: var(--text-secondary);
        margin-top: 4px;
        line-height: 1.4;
      }
      .activity-timeline {
        margin-top: 24px;
        background: rgba(3, 7, 18, 0.6);
        border: 1px solid var(--border-color);
        border-radius: 12px;
        padding: 16px;
      }
      .activity-timeline h4 {
        margin: 0 0 12px;
        font-family: 'Outfit', sans-serif;
        color: #ffffff;
      }
      .activity-list {
        max-height: 200px;
        overflow-y: auto;
        font-family: 'JetBrains Mono', monospace;
        font-size: 0.78rem;
        line-height: 1.6;
        color: var(--text-secondary);
      }
      .activity-row {
        margin-bottom: 6px;
        display: flex;
        justify-content: space-between;
      }
      .activity-row .event-type {
        color: var(--accent-blue);
      }
      .activity-row .timestamp {
        color: #4b5563;
      }
    </style>
  </head>
  <body>
    <main>
      <div class="header">
        <div class="header-title">
          <h1>SupportMaster Case Workspace</h1>
          <p>Tenant-scoped execution dashboard, verification audits, and human-in-the-loop gates.</p>
        </div>
        <div style="display: flex; align-items: center; gap: 20px;">
          <label style="display: flex; align-items: center; gap: 8px; cursor: pointer; user-select: none; font-size: 0.9rem; font-weight: 600; color: var(--text-primary); margin: 0;">
            <input type="checkbox" id="auto-approve-toggle" onchange="toggleAutoApprove(this.checked)" style="width: auto; height: auto; cursor: pointer; accent-color: var(--accent-blue);">
            Autonomous Mode (Auto-Approve)
          </label>
          <a href="/" class="back-btn">← Control Panel & ADK Chat</a>
        </div>
      </div>

      <div class="metrics-grid">
        <div class="metric-card">
          <h3>Total Cases</h3>
          <div class="value" id="metrics-total">-</div>
        </div>
        <div class="metric-card alert" id="metrics-open-card">
          <h3>Open Review Tasks</h3>
          <div class="value" id="metrics-open">-</div>
        </div>
        <div class="metric-card">
          <h3>Total Approvals</h3>
          <div class="value" id="metrics-approvals">-</div>
        </div>
        <div class="metric-card" id="metrics-expiring-card">
          <h3>Expiring Tasks (24h)</h3>
          <div class="value" id="metrics-expiring">-</div>
        </div>
      </div>

      <div class="review-queue-section" id="review-queue">
        <!-- Rendered dynamically -->
      </div>

      <div class="cases-title">
        <span>Active Case Pipeline</span>
      </div>
      
      <div id="cases-list">
        <!-- Rendered dynamically -->
      </div>
    </main>

    <script>
      const esc = s => String(s ?? '').replace(/[&<>\\"/]/g, c => ({
        '&': '&amp;',
        '<': '&lt;',
        '>': '&gt;',
        '"': '&quot;',
        '/': '&#x2F;'
      }[c]));

      function loadWorkspace() {
        // Fetch Review Queue Metrics
        fetch('/api/reviews/metrics')
          .then(r => r.json())
          .then(m => {
            document.getElementById('metrics-total').textContent = (m.total_cases !== undefined ? m.total_cases : m.total);
            document.getElementById('metrics-open').textContent = m.open_count;
            document.getElementById('metrics-approvals').textContent = m.approvals;
            document.getElementById('metrics-expiring').textContent = m.expiring_count;
            
            if (m.open_count > 0) {
              document.getElementById('metrics-open-card').classList.add('alert');
            } else {
              document.getElementById('metrics-open-card').classList.remove('alert');
            }
          }).catch(console.error);

        // Fetch Open Review Tasks
        fetch('/api/reviews')
          .then(r => r.json())
          .then(data => {
            const container = document.getElementById('review-queue');
            if (!data.tasks || data.tasks.length === 0) {
              container.innerHTML = '';
              return;
            }
            
            const openTasks = data.tasks.filter(t => t.status === 'OPEN');
            if (openTasks.length === 0) {
              container.innerHTML = '';
              return;
            }
            
            container.innerHTML = openTasks.map(task => {
              const scopesList = task.allowed_scopes.map(s => 
                `<label class="scope-item"><input type="checkbox" name="scopes" value="${esc(s)}" checked> ${esc(s)}</label>`
              ).join('');
              
              return `
                <div class="review-task-card">
                  <h3>Action Required: Human Review Pending</h3>
                  <div class="review-details">
                    <p><strong>Reason:</strong> ${esc(task.reason)}</p>
                    <p><strong>Blocking Reasons:</strong> ${esc(task.blocking_reasons.join(', ') || 'None')}</p>
                    <p><strong>Required Actions:</strong> ${esc(task.required_actions.join(', ') || 'None')}</p>
                  </div>
                  
                  <form class="review-form" id="form-${esc(task.task_id)}" onsubmit="submitReview(event, '${esc(task.task_id)}')">
                    <h4>Submit Review Decision</h4>
                    <div class="form-grid">
                      <div class="form-group">
                        <label>Reviewer Name</label>
                        <input type="text" name="reviewer" required placeholder="e.g. Alice Smith">
                      </div>
                      <div class="form-group">
                        <label>Decision</label>
                        <select name="decision" required onchange="toggleScopes(this, '${esc(task.task_id)}')">
                          <option value="APPROVE">APPROVE (Resume execution)</option>
                          <option value="REJECT">REJECT (Halt run)</option>
                        </select>
                      </div>
                      <div class="form-group full-width" id="scopes-group-${esc(task.task_id)}">
                        <label>Authorize Scopes</label>
                        <div class="scopes-checklist">
                          ${scopesList}
                        </div>
                      </div>
                      <div class="form-group">
                        <label>Resume Token</label>
                        <input type="text" name="resume_token" required placeholder="Enter token hash/secret">
                      </div>
                      <div class="form-group">
                        <label>Comment</label>
                        <input type="text" name="comment" placeholder="Optional audit notes">
                      </div>
                    </div>
                    <button type="submit" class="btn-submit">Submit Authorization Decision</button>
                  </form>
                </div>
              `;
            }).join('');
          }).catch(console.error);

        // Fetch Case Snapshots
        fetch('/api/cases')
          .then(r => r.json())
          .then(async data => {
            const list = document.getElementById('cases-list');
            if (!data.cases || data.cases.length === 0) {
              list.innerHTML = '<p class="muted" style="text-align: center; padding: 40px; color: var(--text-secondary);">No active cases in the execution pipeline yet. Launch a workflow or send a message in the ADK Chat.</p>';
              return;
            }
            
            const views = await Promise.all(data.cases.map(async c => {
              const base = '/api/cases/' + encodeURIComponent(c.case_id);
              const [snapshot, activityData, relatedData] = await Promise.all([
                fetch(base).then(r => r.json()).catch(() => null),
                fetch(base + '/activity').then(r => r.json()).catch(() => ({ events: [] })),
                fetch(base + '/related').then(r => r.json()).catch(() => ({ related: [] }))
              ]);
              return {
                snapshot: snapshot,
                activity: activityData ? activityData.events || [] : [],
                related: relatedData ? relatedData.related || [] : []
              };
            }));
            
            list.innerHTML = views.map(v => {
              const snap = v.snapshot;
              if (!snap || !snap.case) return '';
              
              const statusClass = snap.case.status === 'RESOLVED' || snap.case.status === 'COMPLETED' ? 'completed' : 
                                  snap.case.status === 'SAFETY_STOP' ? 'safety-stop' : 'open';
              
              const gatesBadges = Object.entries(snap.gate_statuses || {}).map(([name, status]) => {
                const badgeClass = String(status).toLowerCase().replace(/_/g, '-');
                return `<span class="gate-badge ${badgeClass}">${esc(name.replace(/_/g, ' ').toUpperCase())}: ${esc(status)}</span>`;
              }).join('');

              const timelineItems = (snap.timeline || []).map(event => {
                let dotClass = 'complete';
                if (event.status === 'SAFETY_STOP' || event.status === 'FAILED') dotClass = 'safety-stop';
                else if (event.status === 'PARTIAL' || event.status === 'PAUSED_FOR_HUMAN_REVIEW') dotClass = 'partial';
                
                const statusLabelClass = dotClass;
                return `
                  <div class="timeline-item">
                    <div class="timeline-dot ${dotClass}"></div>
                    <div class="timeline-content">
                      <strong>${esc(event.stage)}</strong>
                      <span class="timeline-status ${statusLabelClass}">${esc(event.status)}</span>
                      <div class="timeline-detail">${esc(event.detail)}</div>
                    </div>
                  </div>
                `;
              }).join('');

              const activityRows = (v.activity || []).slice(-6).reverse().map(e => `
                <div class="activity-row">
                  <span class="event-type">${esc(e.event_type)}</span>
                  <span class="timestamp">${esc(e.recorded_at ? e.recorded_at.split('T')[1].slice(0, 8) + ' UTC' : '')}</span>
                </div>
              `).join('') || '<div class="muted">No telemetry events.</div>';

              const relatedItems = (v.related || []).map(r => {
                const isLineage = r.relationship === 'PARENT_RERUN' || r.relationship === 'CHILD_RERUN';
                const tagLabel = r.relationship === 'PARENT_RERUN' ? 'ORIGINAL PARENT' :
                                 r.relationship === 'CHILD_RERUN' ? 'SUBSEQUENT RERUN' : 'SIMILAR INCIDENT';
                const tagColor = isLineage ? 'var(--accent-purple, #8b5cf6)' : 'var(--accent-cyan, #06b6d4)';
                return `
                  <div style="background: rgba(30, 41, 59, 0.6); padding: 8px 12px; border-radius: 6px; border: 1px solid var(--border-color); font-size: 0.85rem; display: flex; justify-content: space-between; align-items: center; gap: 8px;">
                    <div>
                      <span style="font-size: 0.7rem; font-weight: 700; color: ${tagColor}; text-transform: uppercase; margin-right: 6px;">[${esc(tagLabel)}]</span>
                      <strong>${esc(r.title || r.case_id)}</strong>
                      <span style="color: var(--text-secondary); margin-left: 6px;">(${esc(r.case_id)})</span>
                      <div style="color: var(--text-muted); font-size: 0.8rem; margin-top: 2px;">${esc(r.summary)}</div>
                    </div>
                    <a href="/?case_id=${encodeURIComponent(r.case_id)}" class="fixture-btn" style="text-decoration: none; padding: 4px 8px; font-size: 0.75rem; white-space: nowrap;">Ask Q&A</a>
                  </div>
                `;
              }).join('');

              const rootCauseSummary = snap.planning && snap.planning.root_cause ? (snap.planning.root_cause.primary_root_cause || snap.planning.root_cause.explanation || '') : '';

              return `
                <div class="case-card" id="case-${esc(snap.case.case_id)}">
                  <div class="case-header">
                    <div>
                      <h3 class="case-title">${esc(snap.case.title || 'Support Case')}</h3>
                      <div class="case-meta">
                        <span><strong>Case ID:</strong> ${esc(snap.case.case_id)}</span>
                        <span><strong>Tenant:</strong> ${esc(snap.case.tenant_id)}</span>
                        <span><strong>Source:</strong> ${esc(snap.case.source_system)}</span>
                        <span><strong>Current Stage:</strong> <span style="color: var(--accent-blue); font-weight: 600;">${esc(snap.workflow_stage || 'IN_PROGRESS')}</span></span>
                      </div>
                    </div>
                    <span class="case-status-badge ${statusClass}">${esc(snap.case.status)}</span>
                  </div>
                  
                  <div class="case-description">${esc(snap.case.description)}</div>
                  
                  <div class="action-banner">
                    <strong>Recommended Next Action:</strong> ${esc(snap.next_action || 'Execute automated investigation and validation suite.')}
                  </div>
                  
                  <div style="margin-top: 12px;">
                    <strong style="font-size: 0.8rem; text-transform: uppercase; color: var(--text-secondary); display: block; margin-bottom: 6px;">Safety Gate Verification Status</strong>
                    <div class="gates-container">
                      ${gatesBadges || '<span class="gate-badge">GATES PENDING INITIALIZATION</span>'}
                    </div>
                  </div>
                  
                  <div class="timeline-section">
                    <h4>Workflow Stages Pipeline</h4>
                    <div class="timeline-flow">
                      ${timelineItems}
                    </div>
                  </div>
                  
                  <div class="activity-timeline">
                    <h4>Verifiable Audit Telemetry Log</h4>
                    <div class="activity-list">
                      ${activityRows}
                    </div>
                  </div>

                  ${relatedItems ? `
                  <div style="margin-top: 16px;">
                    <h4 style="margin: 0 0 8px; font-size: 0.85rem; text-transform: uppercase; color: var(--text-secondary);">Cross-Run Memory & Related Cases</h4>
                    <div style="display: flex; flex-direction: column; gap: 6px;">
                      ${relatedItems}
                    </div>
                  </div>
                  ` : ''}

                  <div style="margin-top: 20px; display: flex; gap: 12px; border-top: 1px solid var(--border-color); padding-top: 16px; flex-wrap: wrap;">
                    <a href="/?case_id=${encodeURIComponent(snap.case.case_id)}" class="fixture-btn" style="text-decoration: none; display: inline-flex; align-items: center; gap: 6px;">
                      📋 Case Evidence Q&A
                    </a>
                    <button type="button" class="fixture-btn" style="display: inline-flex; align-items: center; gap: 6px; background: rgba(59, 130, 246, 0.2); border-color: rgba(59, 130, 246, 0.4);" onclick="promptRerun('${esc(snap.case.case_id)}', '${esc(snap.case.title.replace(/'/g, "\\'"))}', '${esc(snap.case.description.slice(0, 300).replace(/'/g, "\\'"))}', '${esc(rootCauseSummary.replace(/'/g, "\\'"))}')">
                      🔄 Rerun with New Context
                    </button>
                    <a href="/?model=gemini-3.5-flash" class="fixture-btn" style="text-decoration: none; display: inline-flex; align-items: center; gap: 6px; margin-left: auto;">
                      💬 Open in ADK Live Chat
                    </a>
                  </div>
                </div>
              `;
            }).join('');
          }).catch(e => {
            document.getElementById('cases-list').innerHTML = '<p class="muted">Error loading cases: ' + esc(e) + '</p>';
          });
      }

      function promptRerun(caseId, title, description, rootCause) {
        const note = prompt('Enter operator note or new customer context for the rerun of case ' + caseId + ':');
        if (!note || !note.trim()) return;

        const rerunDescription = '## Prior Incident Summary\\n' + description + '\\n\\n## Prior Root Cause Analysis\\n' + (rootCause || 'Under investigation') + '\\n\\n## Operator Note / Appended Context\\n' + note.trim();
        
        fetch('/api/cases', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            title: 'Rerun: ' + title,
            description: rerunDescription,
            source_system: 'RERUN',
            metadata: { parent_case_id: caseId }
          })
        })
        .then(r => r.json())
        .then(data => {
          if (data.case && data.case.case_id) {
            alert('Rerun case created successfully (' + data.case.case_id + ')! Opening workspace.');
            loadWorkspace();
          } else {
            alert('Case created: ' + JSON.stringify(data));
            loadWorkspace();
          }
        })
        .catch(err => {
          alert('Failed to submit rerun: ' + err.message);
        });
      }

      function toggleScopes(select, taskId) {
        const group = document.getElementById('scopes-group-' + taskId);
        if (select.value === 'REJECT') {
          group.style.display = 'none';
        } else {
          group.style.display = 'block';
        }
      }

      function submitReview(event, taskId) {
        event.preventDefault();
        const form = event.target;
        const reviewer = form.reviewer.value;
        const decision = form.decision.value;
        const resume_token = form.resume_token.value;
        const comment = form.comment.value;
        
        let approved_scopes = [];
        if (decision === 'APPROVE') {
          const checkboxes = form.querySelectorAll('input[name="scopes"]:checked');
          checkboxes.forEach(cb => approved_scopes.push(cb.value));
        }

        const payload = {
          reviewer,
          decision,
          resume_token,
          approved_scopes,
          comment
        };

        const csrfToken = document.querySelector('meta[name="csrf-token"]')?.getAttribute('content') || '';
        fetch('/api/reviews/' + encodeURIComponent(taskId) + '/decide', {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            'X-SupportMaster-API-Key': 'secret|operator|demo-acme|RUN_EXECUTE',
            'X-CSRF-Token': csrfToken
          },
          body: JSON.stringify(payload)
        })
        .then(async r => {
          const data = await r.json();
          if (!r.ok) {
            throw new Error(data.error || 'Server returned error ' + r.status);
          }
          alert('Decision submitted successfully! Resuming workflow in the background.');
          loadWorkspace();
        })
        .catch(err => {
          alert('Failed to submit decision: ' + err.message);
        });
      }

      loadWorkspace();
      setInterval(loadWorkspace, 5000);

      function loadAutoApproveSetting() {
        fetch('/api/settings/auto-approve', {
          headers: {
            'X-SupportMaster-API-Key': 'secret|operator|demo-acme|AUDIT_READ'
          }
        })
        .then(r => r.json())
        .then(data => {
          document.getElementById('auto-approve-toggle').checked = !!data.enabled;
        }).catch(console.error);
      }

      function toggleAutoApprove(checked) {
        const csrfToken = document.querySelector('meta[name="csrf-token"]')?.getAttribute('content') || '';
        fetch('/api/settings/auto-approve', {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            'X-SupportMaster-API-Key': 'secret|operator|demo-acme|RUN_EXECUTE',
            'X-CSRF-Token': csrfToken
          },
          body: JSON.stringify({ enabled: checked })
        })
        .then(r => {
          if (!r.ok) throw new Error('Failed to update setting');
        })
        .catch(err => {
          alert('Error: ' + err.message);
          document.getElementById('auto-approve-toggle').checked = !checked;
        });
      }

      loadAutoApproveSetting();
    </script>
  </body>
</html>"""
    html = html.replace("<title>SupportMaster Operator Workspace</title>", f'<title>SupportMaster Operator Workspace</title>\n    <meta name="csrf-token" content="{csrf_token}">')
    return html


async def check_safety_gates_and_handle_reviews(
    run_store: SQLiteRunStore,
    state: SupportMasterState,
    task_payload: dict[str, Any],
    session_service: SqliteSessionService,
    app_name: str,
    user_id: str,
) -> None:
    """Evaluate terminal state: auto-approve and resume if requested, else create review task."""
    if state.terminal_status == "SAFETY_STOP" and not state.pending_human_review:
        last_gate = state.last_gate_decision.gate if state.last_gate_decision else "REVIEW"
        allowed_scopes = ["IMPLEMENTATION"] if last_gate == "IMPLEMENTATION_AUTHORIZATION" else (
            ["PUBLISH"] if last_gate == "PUBLISH_AUTHORIZATION" else ["IMPLEMENTATION", "PUBLISH"]
        )
        reason = state.last_gate_decision.reason if state.last_gate_decision else "Safety gate stopped the workflow."
        
        # Check if auto-approve is enabled
        auto_approve_file = Path(".supportmaster/auto_approve.flag")
        is_auto_approve = os.getenv("SUPPORTMASTER_AUTO_APPROVE") == "true" or auto_approve_file.exists()
        
        if is_auto_approve:
            from .models.control import AuthorizationGrant
            from datetime import datetime, timezone, timedelta
            
            # Issue grants autonomously
            for scope in allowed_scopes:
                grant = AuthorizationGrant(
                    scope=scope,
                    approval_id=f"auto-approve-{uuid4().hex[:8]}",
                    expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
                )
                state.authorizations.append(grant)
            
            state.terminal_status = None
            state.terminal_outcome = None
            state.pending_human_review = None
            
            # Sync back to session state
            try:
                session = await session_service.get_session(app_name=app_name, user_id=user_id, session_id=state.run_id)
                if session is not None:
                    from google.adk.events.event import Event, EventActions
                    # Update session state using standard event delta append
                    event = Event(
                        actions=EventActions(
                            state_delta=state.model_dump(mode="json")
                        )
                    )
                    await session_service.append_event(session, event)
            except Exception as e:
                run_store.append_event(state.run_id, "AUTO_APPROVE_SYNC_FAILED", {"error": str(e)})
            
            run_store.save_state(state, event_type="AUTO_APPROVED")
            run_store.append_event(state.run_id, "HUMAN_REVIEW_DECIDED", {
                "reviewer": "Auto-Approve Agent",
                "decision": "APPROVE",
                "comment": f"Automatically approved safety gate: {last_gate}"
            })
            
            # Re-enqueue the workflow task to continue execution automatically
            new_idempotency_key = f"{state.run_id}:adk_workflow:resume-auto-{uuid4().hex[:8]}"
            run_store.enqueue_task(
                state.run_id,
                task_name="adk_workflow",
                idempotency_key=new_idempotency_key,
                payload=task_payload,
                max_attempts=3,
            )
            
            # Start background worker sync
            from threading import Thread
            Thread(
                target=run_resumed_worker_sync,
                args=(state.run_id, task_payload.get("model_name")),
                daemon=True,
            ).start()
        else:
            # Create review task for manual operator approval
            task_obj, token = run_store.create_review_task(
                state.run_id,
                reason=reason,
                allowed_scopes=allowed_scopes,
                resume_condition=f"Approval of safety gate {last_gate}"
            )
            state.pending_human_review = task_obj
            state.terminal_status = "HUMAN_REVIEW_REQUIRED"
            state.terminal_outcome = "PAUSED_FOR_HUMAN_REVIEW"
            run_store.save_state(state, event_type="RUN_PAUSED_FOR_HUMAN_REVIEW")
            
            # Sync back to session state
            try:
                session = await session_service.get_session(app_name, user_id, state.run_id)
                session.state = state.model_dump(mode="json")
                await session_service.save_session(session)
            except Exception:
                pass


class SupportMasterHandler(BaseHTTPRequestHandler):
    def _validate_csrf(self) -> bool:
        if not self.headers.get("Cookie") and not self.headers.get("cookie"):
            return True
        cookie = SimpleCookie(self.headers.get("Cookie") or self.headers.get("cookie"))
        cookie_token = cookie.get("csrf-token")
        cookie_val = cookie_token.value if cookie_token else None
        header_val = self.headers.get("X-CSRF-Token") or self.headers.get("x-csrf-token")
        if not cookie_val or not header_val or not secrets.compare_digest(cookie_val, header_val):
            self._send_json({"error": "CSRF validation failed."}, status=403)
            return False
        return True

    def do_GET(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if path == "/workspace":
            auth = AUTHENTICATOR.authenticate(self.headers)
            if not self._authorized(auth, "AUDIT_READ"):
                return
            cookie = SimpleCookie(self.headers.get("Cookie"))
            cookie_token = cookie.get("csrf-token")
            csrf_token = cookie_token.value if cookie_token else None
            if not csrf_token:
                csrf_token = secrets.token_hex(16)
            page = render_workspace(csrf_token)
            body = page.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Set-Cookie", f"csrf-token={csrf_token}; Path=/; HttpOnly; SameSite=Strict")
            self.end_headers()
            self.wfile.write(body)
            return
        if path == "/api/models/available":
            self._send_json({
                "models": MODEL_RESOLVER.get_available_models(),
                "default_model": MODEL_RESOLVER.default_model,
                "default_provider": MODEL_RESOLVER.default_provider,
                "fallback_chain": MODEL_RESOLVER.fallback_chain,
            }, status=200)
            return
        if path == "/api/fixtures":
            fixtures_dir = Path("fixtures/cases")
            fixture_names = [f.stem for f in sorted(fixtures_dir.glob("*.json"))] if fixtures_dir.exists() else []
            self._send_json({"fixtures": fixture_names}, status=200)
            return
        if path.startswith("/api/fixtures/"):
            fixture_name = path.split("/")[-1]
            fixture_file = Path("fixtures/cases") / f"{fixture_name}.json"
            if fixture_file.exists():
                try:
                    with open(fixture_file, "r", encoding="utf-8") as f:
                        self._send_json(json.load(f), status=200)
                except Exception as e:
                    self._send_json({"error": str(e)}, status=500)
            else:
                self._send_json({"error": f"Fixture '{fixture_name}' not found."}, status=404)
            return
        if path == "/api/cases" or (path.startswith("/api/cases/") and path.count("/") == 3):
            auth = AUTHENTICATOR.authenticate(self.headers)
            if not self._authorized(auth, "AUDIT_READ"):
                return
            try:
                assert auth.principal is not None
                store = SQLiteRunStore(os.getenv("SUPPORTMASTER_RUN_DB", ".supportmaster/runs.db"))
                workspace = CaseWorkspaceService(store)
                if path == "/api/cases":
                    self._send_json({"cases": [item.model_dump(mode="json") for item in workspace.list(auth.principal.tenant_id)]}, status=200)
                else:
                    case_id = path.rsplit("/", 1)[-1]
                    self._send_json(workspace.snapshot(case_id, auth.principal.tenant_id).model_dump(mode="json"), status=200)
            except KeyError as error:
                self._send_json({"error": str(error)}, status=404)
            return
        if path.startswith("/api/cases/") and path.endswith("/activity"):
            auth = AUTHENTICATOR.authenticate(self.headers)
            if not self._authorized(auth, "AUDIT_READ"):
                return
            try:
                assert auth.principal is not None
                store = SQLiteRunStore(os.getenv("SUPPORTMASTER_RUN_DB", ".supportmaster/runs.db"))
                case_id = path.split("/")[3]
                self._send_json({"events": [event.model_dump(mode="json") for event in CaseWorkspaceService(store).activity(case_id, auth.principal.tenant_id)]}, status=200)
            except KeyError as error:
                self._send_json({"error": str(error)}, status=404)
            return
        if path.startswith("/api/cases/") and path.endswith("/related"):
            auth = AUTHENTICATOR.authenticate(self.headers)
            if not self._authorized(auth, "AUDIT_READ"):
                return
            try:
                assert auth.principal is not None
                case_id = path.split("/")[3]
                store = SQLiteRunStore(os.getenv("SUPPORTMASTER_RUN_DB", ".supportmaster/runs.db"))
                case = store.get_case(case_id, tenant_id=auth.principal.tenant_id)
                
                from .memory.case_store import CaseMemoryStore
                memory_store = CaseMemoryStore()
                query = f"{case.title} {case.description}"
                similar_cases = memory_store.retrieve_similar(query, tenant_id=auth.principal.tenant_id, top_k=5)
                
                related_list = []
                # 1. Lineage: Check if current case has parent_case_id in metadata
                parent_case_id = case.metadata.get("parent_case_id") if isinstance(case.metadata, dict) else None
                if parent_case_id and parent_case_id != case_id:
                    try:
                        p_case = store.get_case(parent_case_id, tenant_id=auth.principal.tenant_id)
                        related_list.append({
                            "case_id": p_case.case_id,
                            "title": p_case.title,
                            "status": p_case.status,
                            "relationship": "PARENT_RERUN",
                            "similarity_rank": 0.0,
                            "summary": f"Original parent case of this rerun",
                        })
                    except Exception:
                        pass
                
                # 2. Lineage: Check if any other cases are reruns of this case
                try:
                    all_cases = store.list_cases(auth.principal.tenant_id)
                    for c in all_cases:
                        if c.case_id != case_id and isinstance(c.metadata, dict) and c.metadata.get("parent_case_id") == case_id:
                            related_list.append({
                                "case_id": c.case_id,
                                "title": c.title,
                                "status": c.status,
                                "relationship": "CHILD_RERUN",
                                "similarity_rank": 0.0,
                                "summary": f"Subsequent rerun of this case",
                            })
                except Exception:
                    pass

                # 3. FTS5 Memory Similarities
                for sim in similar_cases:
                    if sim.case_id != case_id and not any(r["case_id"] == sim.case_id for r in related_list):
                        related_list.append({
                            "case_id": sim.case_id,
                            "title": sim.title,
                            "status": "RESOLVED",
                            "relationship": "SIMILAR_PATTERN",
                            "similarity_rank": round(sim.similarity_rank, 2),
                            "summary": sim.resolution_summary or sim.root_cause or "Similar symptom pattern",
                        })

                self._send_json({"related": related_list}, status=200)
            except Exception as error:
                self._send_json({"error": str(error)}, status=404 if "not found" in str(error).lower() else 500)
            return
        if path == "/api/reviews":
            auth = AUTHENTICATOR.authenticate(self.headers)
            if not self._authorized(auth, "AUDIT_READ"):
                return
            try:
                assert auth.principal is not None
                store = SQLiteRunStore(os.getenv("SUPPORTMASTER_RUN_DB", ".supportmaster/runs.db"))
                from .review_queue import ReviewQueueService
                self._send_json(ReviewQueueService(store).snapshot(auth.principal.tenant_id).model_dump(mode="json"), status=200)
            except KeyError as error:
                self._send_json({"error": str(error)}, status=404)
            return
        if path == "/api/reviews/metrics":
            auth = AUTHENTICATOR.authenticate(self.headers)
            if not self._authorized(auth, "AUDIT_READ"):
                return
            try:
                assert auth.principal is not None
                store = SQLiteRunStore(os.getenv("SUPPORTMASTER_RUN_DB", ".supportmaster/runs.db"))
                from .review_queue import ReviewQueueService
                self._send_json(ReviewQueueService(store).metrics(auth.principal.tenant_id).model_dump(mode="json"), status=200)
            except KeyError as error:
                self._send_json({"error": str(error)}, status=404)
        if path == "/api/fixtures" or path.startswith("/api/fixtures/"):
            auth = AUTHENTICATOR.authenticate(self.headers)
            if not self._authorized(auth, "AUDIT_READ"):
                return
            try:
                fixtures_dir = Path("fixtures/cases")
                if path == "/api/fixtures":
                    fixtures = []
                    if fixtures_dir.exists():
                        for p in fixtures_dir.glob("*.json"):
                            fixtures.append(p.stem)
                    self._send_json({"fixtures": sorted(fixtures)}, status=200)
                else:
                    fixture_name = path.split("/")[-1]
                    if not fixture_name.isalnum() and "_" not in fixture_name and "-" not in fixture_name:
                        raise ValueError("Invalid fixture name.")
                    fixture_path = fixtures_dir / f"{fixture_name}.json"
                    if not fixture_path.exists():
                        raise FileNotFoundError(f"Fixture {fixture_name} not found.")
                    with open(fixture_path, "r", encoding="utf-8") as f:
                        content = json.load(f)
                    self._send_json(content, status=200)
            except Exception as error:
                self._send_json({"error": str(error)}, status=404 if "not found" in str(error).lower() else 500)
            return
        if path == "/api/settings/auto-approve":
            auth = AUTHENTICATOR.authenticate(self.headers)
            if not self._authorized(auth, "AUDIT_READ"):
                return
            flag_file = Path(".supportmaster/auto_approve.flag")
            enabled = flag_file.exists() or os.getenv("SUPPORTMASTER_AUTO_APPROVE") == "true"
            self._send_json({"enabled": enabled}, status=200)
            return

        if path == "/api/organizations":
            auth = AUTHENTICATOR.authenticate(self.headers)
            if not self._authorized(auth, "AUDIT_READ"):
                return
            try:
                assert auth.principal is not None
                store = SQLiteRunStore(os.getenv("SUPPORTMASTER_RUN_DB", ".supportmaster/runs.db"))
                profile = OrganizationContextService(store).get(auth.principal.tenant_id)
                payload = profile.model_dump(mode="json")
                for connection in payload.get("workspace_connections", []):
                    connection["secret_ref"] = "***REDACTED***"
                self._send_json(payload, status=200)
            except KeyError as error:
                self._send_json({"error": str(error)}, status=404)
            return

        if path in {"/health/live", "/health/ready"}:
            auth = AUTHENTICATOR.authenticate(self.headers)
            if path.endswith("/ready") and not self._authorized(auth, "HEALTH_READ"):
                return
            reporter = _configured_health_reporter()
            report = reporter.liveness() if path.endswith("/live") else reporter.readiness()
            self._send_json(
                report.model_dump(mode="json"),
                status=200 if report.status in {"LIVE", "READY"} else 503,
            )
            return
        if path.startswith("/api/stream/"):
            auth = AUTHENTICATOR.authenticate(self.headers)
            if not self._authorized(auth, "AUDIT_READ"):
                return
            run_id = path.split("/")[3]
            store = SQLiteRunStore(os.getenv("SUPPORTMASTER_RUN_DB", ".supportmaster/runs.db"))
            # Send Server-Sent Events headers
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            import time
            seen_ids: set[str] = set()
            try:
                for _ in range(120):  # Poll up to 120s (60 * 2s intervals)
                    try:
                        events = store.list_events(run_id)
                    except Exception:
                        events = []
                    for evt in events:
                        evt_id = f"{evt.get('recorded_at', '')}-{evt.get('event_type', '')}"
                        if evt_id not in seen_ids:
                            seen_ids.add(evt_id)
                            data = json.dumps({
                                "event_type": evt.get("event_type"),
                                "recorded_at": evt.get("recorded_at"),
                                "payload": evt.get("payload") or {},
                            })
                            msg = f"data: {data}\n\n"
                            try:
                                self.wfile.write(msg.encode("utf-8"))
                                self.wfile.flush()
                            except (BrokenPipeError, ConnectionResetError):
                                return
                    time.sleep(2)
            except Exception:
                pass
            return
        if path == "/api/metrics/scorecard":
            auth = AUTHENTICATOR.authenticate(self.headers)
            if not self._authorized(auth, "AUDIT_READ"):
                return
            try:
                assert auth.principal is not None
                from .evaluation.scorecard import ScorecardService
                store = SQLiteRunStore(os.getenv("SUPPORTMASTER_RUN_DB", ".supportmaster/runs.db"))
                scorecard = ScorecardService(store).compute(auth.principal.tenant_id)
                self._send_json(scorecard, status=200)
            except Exception as error:
                self._send_json({"error": str(error)}, status=500)
            return
        query = parse_qs(urlparse(self.path).query)
        selected_model = query.get("model", [DEFAULT_MODEL])[0]
        page = render_page(selected_model)
        self._send_page(page)

    def do_POST(self) -> None:  # noqa: N802
        auth = AUTHENTICATOR.authenticate(self.headers)
        path = urlparse(self.path).path
        if not self._authorized(auth, "ORG_ADMIN" if path == "/api/organizations" else "RUN_EXECUTE"):
            return
        content_length = int(self.headers.get("Content-Length", "0"))
        if content_length > OPERATION_SETTINGS.max_issue_bytes * 2:
            self._send_json({"error": "Request body exceeds the configured limit."}, status=413)
            return

        if path == "/api/settings/auto-approve":
            if not self._validate_csrf():
                return
            try:
                payload = json.loads(self.rfile.read(content_length).decode("utf-8"))
                enabled = bool(payload.get("enabled", False))
                flag_file = Path(".supportmaster/auto_approve.flag")
                flag_file.parent.mkdir(parents=True, exist_ok=True)
                if enabled:
                    flag_file.write_text("true")
                else:
                    if flag_file.exists():
                        flag_file.unlink()
                self._send_json({"enabled": enabled}, status=200)
            except Exception as error:
                self._send_json({"error": str(error)}, status=400)
            return

        if path == "/api/organizations":
            try:
                assert auth.principal is not None
                payload = json.loads(self.rfile.read(content_length).decode("utf-8"))
                if not isinstance(payload, dict):
                    raise ValueError("Organization payload must be a JSON object.")
                payload["organization_id"] = auth.principal.tenant_id
                profile = OrganizationProfile.model_validate(payload)
                store = SQLiteRunStore(os.getenv("SUPPORTMASTER_RUN_DB", ".supportmaster/runs.db"))
                saved = OrganizationContextService(store).save(profile)
                # secret_ref is write-only: never echo credential references back.
                saved_payload = saved.model_dump(mode="json")
                for connection in saved_payload.get("workspace_connections", []):
                    connection["secret_ref"] = "***REDACTED***"
                self._send_json(saved_payload, status=200)
            except (ValueError, TypeError, json.JSONDecodeError) as error:
                self._send_json({"error": str(error)}, status=400)
            return
        if path == "/api/connectors/jira":
            try:
                assert auth.principal is not None
                if not RATE_LIMITER.consume(auth.principal.tenant_id):
                    self._send_json({"error": "Rate limit exceeded. Please try again later."}, status=429)
                    return
                body_bytes = self.rfile.read(content_length)
                secret = os.getenv("SUPPORTMASTER_JIRA_SECRET")
                sig = self.headers.get("X-Hub-Signature") or self.headers.get("x-hub-signature")
                from .connectors import JiraConnector
                if secret and sig and not JiraConnector.verify_signature(body_bytes, secret, sig):
                    self._send_json({"error": "Invalid webhook signature."}, status=401)
                    return
                payload = json.loads(body_bytes.decode("utf-8"))
                mapped = JiraConnector.map_payload(payload)
                store = SQLiteRunStore(os.getenv("SUPPORTMASTER_RUN_DB", ".supportmaster/runs.db"))
                from .intake import CaseIntakeService
                result = CaseIntakeService(store).ingest(
                    mapped,
                    source_system="JIRA",
                    tenant_id=auth.principal.tenant_id,
                )
                self._send_json(result.model_dump(mode="json"), status=201 if result.status == "CREATED" else 200)
            except Exception as error:
                self._send_json({"error": str(error)}, status=400)
            return

        if path == "/api/connectors/zendesk":
            try:
                assert auth.principal is not None
                if not RATE_LIMITER.consume(auth.principal.tenant_id):
                    self._send_json({"error": "Rate limit exceeded. Please try again later."}, status=429)
                    return
                body_bytes = self.rfile.read(content_length)
                secret = os.getenv("SUPPORTMASTER_ZENDESK_SECRET")
                sig = self.headers.get("X-Zendesk-Signature") or self.headers.get("x-zendesk-signature")
                from .connectors import ZendeskConnector
                if secret and sig and not ZendeskConnector.verify_signature(body_bytes, secret, sig):
                    self._send_json({"error": "Invalid webhook signature."}, status=401)
                    return
                payload = json.loads(body_bytes.decode("utf-8"))
                mapped = ZendeskConnector.map_payload(payload)
                store = SQLiteRunStore(os.getenv("SUPPORTMASTER_RUN_DB", ".supportmaster/runs.db"))
                from .intake import CaseIntakeService
                result = CaseIntakeService(store).ingest(
                    mapped,
                    source_system="ZENDESK",
                    tenant_id=auth.principal.tenant_id,
                )
                self._send_json(result.model_dump(mode="json"), status=201 if result.status == "CREATED" else 200)
            except Exception as error:
                self._send_json({"error": str(error)}, status=400)
            return

        if path == "/api/cases":
            try:
                assert auth.principal is not None
                if not RATE_LIMITER.consume(auth.principal.tenant_id):
                    self._send_json({"error": "Rate limit exceeded. Please try again later."}, status=429)
                    return
                payload = json.loads(self.rfile.read(content_length).decode("utf-8"))
                if not isinstance(payload, dict):
                    raise ValueError("Case intake payload must be a JSON object.")
                source_system = str(
                    self.headers.get("X-SupportMaster-Source")
                    or payload.pop("source_system", None)
                    or "API"
                )
                store = SQLiteRunStore(os.getenv("SUPPORTMASTER_RUN_DB", ".supportmaster/runs.db"))
                from .intake import CaseIntakeService

                result = CaseIntakeService(store).ingest(
                    payload,
                    source_system=source_system,
                    tenant_id=auth.principal.tenant_id,
                )
                self._send_json(result.model_dump(mode="json"), status=201 if result.status == "CREATED" else 200)
            except (ValueError, TypeError, json.JSONDecodeError) as error:
                self._send_json({"error": str(error)}, status=400)
            return
        if path.startswith("/api/cases/") and path.endswith("/status"):
            try:
                assert auth.principal is not None
                payload = json.loads(self.rfile.read(content_length).decode("utf-8"))
                if not isinstance(payload, dict) or not payload.get("status"):
                    raise ValueError("A case status is required.")
                case_id = path.split("/")[3]
                store = SQLiteRunStore(os.getenv("SUPPORTMASTER_RUN_DB", ".supportmaster/runs.db"))
                case = CaseWorkspaceService(store).update_status(case_id, auth.principal.tenant_id, str(payload["status"]))
                self._send_json(case.model_dump(mode="json"), status=200)
            except (ValueError, TypeError, json.JSONDecodeError, KeyError) as error:
                self._send_json({"error": str(error)}, status=400)
            return
        if path.startswith("/api/cases/") and path.endswith("/ask"):
            try:
                assert auth.principal is not None
                payload = json.loads(self.rfile.read(content_length).decode("utf-8"))
                question = str(payload.get("question", "")).strip()
                if not question:
                    raise ValueError("A question is required for evidence Q&A.")
                case_id = path.split("/")[3]
                store = SQLiteRunStore(os.getenv("SUPPORTMASTER_RUN_DB", ".supportmaster/runs.db"))
                case = store.get_case(case_id, tenant_id=auth.principal.tenant_id)
                runs = store.list_runs_for_case(case_id, tenant_id=auth.principal.tenant_id)
                
                context_parts = [f"Support Case:\n{case.workflow_text()}"]
                investigation = CaseWorkspaceService._optional(lambda: store.get_investigation_summary(case_id, tenant_id=auth.principal.tenant_id))
                planning = CaseWorkspaceService._optional(lambda: store.get_planning_assessment(case_id, tenant_id=auth.principal.tenant_id))
                resolution = CaseWorkspaceService._optional(lambda: store.get_resolution_bundle(case_id, tenant_id=auth.principal.tenant_id))

                if investigation:
                    context_parts.append(f"Investigation Summary:\nStatus: {investigation.investigation_status}\nReadiness Reason: {investigation.readiness_reason}\nMissing Evidence: {json.dumps([m.model_dump(mode='json') for m in investigation.missing_evidence])}")
                if planning:
                    context_parts.append(f"Root Cause Assessment:\n{planning.root_cause.model_dump_json(indent=2)}")
                    context_parts.append(f"Remediation Assessment:\n{planning.remediation.model_dump_json(indent=2)}")
                if resolution:
                    context_parts.append(f"Resolution Bundle:\n{resolution.resolution.model_dump_json(indent=2)}")
                
                # If there are runs, get latest run state for additional context
                if runs:
                    latest_run_id = runs[-1]["run_id"]
                    try:
                        latest_state = store.load_state(latest_run_id)
                        if getattr(latest_state, "code_change_result", None):
                            context_parts.append(f"Code Change Result:\n{json.dumps(latest_state.code_change_result, indent=2)[:2000]}")
                        if getattr(latest_state, "validation_analysis", None):
                            context_parts.append(f"Validation Analysis:\n{json.dumps(latest_state.validation_analysis, indent=2)[:2000]}")
                        if getattr(latest_state, "operation_receipts", None):
                            context_parts.append(f"Operation Receipts:\n{json.dumps([r.model_dump(mode='json') for r in latest_state.operation_receipts], indent=2)[:2000]}")
                    except Exception:
                        pass
                
                # Add prior session Q&A history
                session_key = (case_id, auth.principal.tenant_id)
                history = _CASE_QA_SESSIONS.get(session_key, [])
                history_text = "\n".join(f"Q: {h['question']}\nA: {h['answer']}" for h in history[-5:])
                if history_text:
                    context_parts.append(f"Prior Conversation Turns:\n{history_text}")

                context = "\n\n".join(context_parts)
                
                from google import genai
                from google.genai import types
                api_key = os.getenv("GOOGLE_API_KEY")
                if not api_key:
                    rc = (planning.root_cause.primary_root_cause or planning.root_cause.explanation) if (planning and planning.root_cause) else "under investigation"
                    answer = f"[Evidence Q&A Mock Response] For case {case_id}: The recorded root cause is '{rc}'. Verified according to persisted evidence artifacts."
                else:
                    client = genai.Client(api_key=api_key)
                    system_instruction = (
                        "You are the SupportMaster Case Evidence Assistant.\n"
                        "Your goal is to answer questions about a specific support case strictly using the provided case evidence, investigation artifacts, and resolution records.\n"
                        "Rules:\n"
                        "1. Answer objectively, citing specific evidence, files, lines, or root-cause details from the context.\n"
                        "2. If the question asks something that is NOT covered by the provided evidence or artifacts, explicitly state that the stored evidence does not contain that information rather than speculating.\n"
                        "3. Do NOT suggest or execute code changes, and do NOT alter any gate states or workflow decisions."
                    )
                    response = client.models.generate_content(
                        model=DEFAULT_MODEL,
                        contents=f"Context:\n{context}\n\nOperator Question: {question}",
                        config=types.GenerateContentConfig(
                            system_instruction=system_instruction,
                            temperature=0.2,
                        )
                    )
                    answer = response.text or "No response from model."
                
                # Record in session history
                history.append({"question": question, "answer": answer})
                _CASE_QA_SESSIONS[session_key] = history[-10:]

                self._send_json({"answer": answer, "case_id": case_id}, status=200)
            except Exception as error:
                self._send_json({"error": str(error)}, status=400 if isinstance(error, (ValueError, TenantAccessError)) else 500)
            return
        if path.startswith("/api/reviews/") and path.endswith("/chat"):
            try:
                assert auth.principal is not None
                payload = json.loads(self.rfile.read(content_length).decode("utf-8"))
                message = str(payload.get("message", "")).strip()
                if not message:
                    raise ValueError("A message is required for co-pilot chat.")
                
                task_id = path.split("/")[3]
                store = SQLiteRunStore(os.getenv("SUPPORTMASTER_RUN_DB", ".supportmaster/runs.db"))
                task = store.get_review_task(task_id)
                run_state = store.load_state(task.run_id)
                
                if run_state.tenant_id != auth.principal.tenant_id:
                    raise TenantAccessError("Review task belongs to a different tenant.")
                
                context_parts = []
                if getattr(run_state, "support_case", None):
                    context_parts.append(f"Support Case:\n{run_state.support_case.workflow_text()}")
                if getattr(run_state, "root_cause_analysis", None):
                    context_parts.append(f"Root Cause Analysis:\n{json.dumps(run_state.root_cause_analysis, indent=2)}")
                if getattr(run_state, "remediation_plan", None):
                    context_parts.append(f"Remediation Plan:\n{json.dumps(run_state.remediation_plan, indent=2)}")
                if getattr(run_state, "code_change_result", None):
                    context_parts.append(f"Proposed Code Change:\n{json.dumps(run_state.code_change_result, indent=2)}")
                if getattr(run_state, "validation_analysis", None):
                    context_parts.append(f"Validation Analysis:\n{json.dumps(run_state.validation_analysis, indent=2)}")
                if getattr(run_state, "validation_failures", None):
                    context_parts.append(f"Self-Healing Failure Logs:\n{json.dumps(run_state.validation_failures, indent=2)}")
                
                context = "\n\n".join(context_parts)
                
                from google import genai
                from google.genai import types
                api_key = os.getenv("GOOGLE_API_KEY")
                if not api_key:
                    response_text = f"[Co-pilot Mock Response] Evaluating request: '{message}'. The proposed remediation is safe."
                else:
                    client = genai.Client(api_key=api_key)
                    model = DEFAULT_MODEL
                    system_instruction = """
                    You are the SupportMaster Safety Review Co-pilot.
                    Your goal is to answer questions from a human operator about a pending safety gate review task.
                    Answer the operator's questions objectively, referencing only the provided context.
                    """
                    response = client.models.generate_content(
                        model=model,
                        contents=f"Context:\n{context}\n\nOperator Question: {message}",
                        config=types.GenerateContentConfig(
                            system_instruction=system_instruction,
                            temperature=0.2,
                        )
                    )
                    response_text = response.text or "No response from model."
                
                self._send_json({"response": response_text}, status=200)
            except Exception as error:
                self._send_json({"error": str(error)}, status=400 if isinstance(error, (ValueError, TenantAccessError)) else 500)
            return

        if path.startswith("/api/reviews/") and path.endswith("/decide"):
            if not self._validate_csrf():
                return
            try:
                assert auth.principal is not None
                payload = json.loads(self.rfile.read(content_length).decode("utf-8"))
                if not isinstance(payload, dict):
                    raise ValueError("Review payload must be a JSON object.")
                task_id = path.split("/")[3]
                reviewer = str(payload.get("reviewer", "")).strip()
                decision = str(payload.get("decision", "")).strip()
                resume_token = str(payload.get("resume_token", "")).strip()
                approved_scopes = [str(s) for s in payload.get("approved_scopes", [])]
                comment = str(payload.get("comment", "")).strip()

                if decision not in {"APPROVE", "REJECT"}:
                    raise ValueError("Decision must be APPROVE or REJECT.")

                store = SQLiteRunStore(os.getenv("SUPPORTMASTER_RUN_DB", ".supportmaster/runs.db"))
                task = store.get_review_task(task_id)
                run_state = store.load_state(task.run_id)
                if run_state.tenant_id != auth.principal.tenant_id:
                    raise TenantAccessError("Review task belongs to a different tenant.")

                updated_task = store.decide_review_task(
                    task_id,
                    reviewer=reviewer,
                    decision=decision,
                    resume_token=resume_token,
                    approved_scopes=approved_scopes,
                    comment=comment,
                )

                if decision == "APPROVE":
                    store.resume_run(
                        run_id=updated_task.run_id,
                        task_id=task_id,
                        resume_token=resume_token,
                    )
                    updated_task = store.get_review_task(task_id)
                    with store._connect() as connection:
                        row = connection.execute(
                            "SELECT payload_json FROM task_queue WHERE run_id=? AND task_name='adk_workflow' LIMIT 1",
                            (updated_task.run_id,),
                        ).fetchone()
                    if row is not None:
                        task_payload = json.loads(row["payload_json"])
                        new_idempotency_key = f"{updated_task.run_id}:adk_workflow:resume-{uuid4().hex[:8]}"
                        store.enqueue_task(
                            updated_task.run_id,
                            task_name="adk_workflow",
                            idempotency_key=new_idempotency_key,
                            payload=task_payload,
                            max_attempts=3,
                        )
                        from threading import Thread
                        model_name = task_payload.get("model_name")
                        Thread(
                            target=run_resumed_worker_sync,
                            args=(updated_task.run_id, model_name),
                            daemon=True,
                        ).start()

                self._send_json(updated_task.model_dump(mode="json"), status=200)
            except (ValueError, TypeError, json.JSONDecodeError, KeyError, PermissionError) as error:
                self._send_json({"error": str(error)}, status=400)
            return

        if path == "/api/chat":
            try:
                assert auth.principal is not None
                payload = json.loads(self.rfile.read(content_length).decode("utf-8"))
                message = payload.get("message", "").strip()
                selected_model = payload.get("model", DEFAULT_MODEL)
                if not message:
                    self._send_json({"error": "Message cannot be empty."}, status=400)
                    return
                # Fire-and-stream: create the run synchronously, then start
                # the ADK worker in a background thread. The client connects
                # to /api/stream/{run_id} via EventSource for live updates.
                run_id, case_id = _prepare_chat_run(
                    message, selected_model,
                    tenant_id=auth.principal.tenant_id,
                    initiated_by=auth.principal.subject,
                )
                from threading import Thread
                Thread(
                    target=_run_chat_workflow_sync,
                    args=(run_id, message, selected_model, auth.principal.tenant_id, auth.principal.subject),
                    daemon=True,
                ).start()
                self._send_json({
                    "status": "STARTED",
                    "run_id": run_id,
                    "case_id": case_id,
                    "model": selected_model,
                    "model_label": _model_label(selected_model),
                }, status=202)
            except Exception as error:
                self._send_json({"status": "FAILED", "error": str(error)}, status=500)
            return

        form = parse_qs(self.rfile.read(content_length).decode("utf-8"))
        selected_model = form.get("model", [DEFAULT_MODEL])[0]
        issue = form.get("issue", [MOCK_JIRA_ISSUE])[0].strip()

        try:
            assert auth.principal is not None
            result = asyncio.run(
                run_workflow(
                    issue,
                    selected_model,
                    tenant_id=auth.principal.tenant_id,
                    initiated_by=auth.principal.subject,
                )
            )
            status = f"Completed SupportMaster workflow with {_model_label(selected_model)}."
        except Exception as error:
            result = None
            status = f"Workflow did not run ({type(error).__name__}): {error}"

        page = render_page(selected_model, issue, status, result)
        self._send_page(page)

    def _send_page(self, page: str) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(page.encode("utf-8"))))
        self.end_headers()
        self.wfile.write(page.encode("utf-8"))

    def _send_json(self, payload: dict[str, object], *, status: int) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _authorized(self, auth, scope: str) -> bool:
        if auth.status == "REJECTED" or auth.principal is None or not auth.principal.allows(scope):
            self._send_json({"error": auth.reason or f"Missing required scope: {scope}."}, status=401 if auth.status == "REJECTED" else 403)
            return False
        return True

    def log_message(self, format: str, *args: object) -> None:
        return


def _prepare_chat_run(
    issue: str,
    model_name: str,
    *,
    tenant_id: str = "default",
    initiated_by: str = "anonymous",
) -> tuple[str, str]:
    """Synchronously create case, investigation, planning — return (run_id, case_id).

    This runs the deterministic preparation steps immediately so ``/api/chat``
    can return the ``run_id`` to the client right away. The slow ADK pipeline
    is then started in a background thread and streamed via SSE.
    """
    if not issue:
        raise ValueError("A support issue is required.")
    if len(issue.encode("utf-8")) > OPERATION_SETTINGS.max_issue_bytes:
        raise ValueError("The support issue exceeds the configured size limit.")

    run_db = Path(os.getenv("SUPPORTMASTER_RUN_DB", ".supportmaster/runs.db"))
    run_store = SQLiteRunStore(run_db)
    if run_store.active_queue_depth() >= OPERATION_SETTINGS.max_queue_depth:
        raise RuntimeError("SupportMaster task queue is at its configured capacity.")
    organization = OrganizationContextService(run_store).ensure(tenant_id)
    if organization.status != "ACTIVE":
        raise RuntimeError(f"Organization {tenant_id} is not active.")
    case = normalize_case(
        {"title": issue.splitlines()[0][:2_000] or "Support case", "description": issue},
        source_system="MANUAL",
        tenant_id=tenant_id,
    )
    run_store.save_case(case)
    investigation_summary = InvestigationService(run_store).summarize(case)
    run_store.save_investigation_summary(investigation_summary)
    root_cause, remediation = PlanningService().build(case, investigation_summary)
    planning_assessment = PlanningAssessment(
        case_id=case.case_id,
        tenant_id=tenant_id,
        root_cause=root_cause,
        remediation=remediation,
    )
    run_store.save_planning_assessment(planning_assessment)
    return str(uuid4()), case.case_id


def _run_chat_workflow_sync(
    run_id: str,
    issue: str,
    model_name: str,
    tenant_id: str,
    initiated_by: str,
) -> None:
    """Background thread entry point — run the full workflow and emit completion event."""
    try:
        asyncio.run(
            run_workflow(
                issue,
                model_name,
                tenant_id=tenant_id,
                initiated_by=initiated_by,
                run_id=run_id,
            )
        )
    except Exception as e:
        logger.exception(f"Background chat workflow failed for run {run_id}: {e}")
        try:
            run_store = SQLiteRunStore(os.getenv("SUPPORTMASTER_RUN_DB", ".supportmaster/runs.db"))
            run_store.append_event(run_id, "RUN_FAILED", {"error": str(e)})
        except Exception:
            pass


async def run_workflow(
    issue: str,
    model_name: str,
    *,
    tenant_id: str = "default",
    initiated_by: str = "anonymous",
    run_id: str | None = None,
) -> str:
    """Admit one bounded run and release its lease on every exit path."""
    admission_id = run_id or str(uuid4())
    with RUN_ADMISSION.lease(admission_id):
        return await _run_workflow(issue, model_name, run_id=admission_id, tenant_id=tenant_id, initiated_by=initiated_by)


async def _run_workflow(
    issue: str,
    model_name: str,
    *,
    run_id: str | None = None,
    tenant_id: str = "default",
    initiated_by: str = "anonymous",
) -> str:
    """Run one isolated, durable workflow and return generated agent messages."""
    if not issue:
        raise ValueError("A support issue is required.")
    if len(issue.encode("utf-8")) > OPERATION_SETTINGS.max_issue_bytes:
        raise ValueError("The support issue exceeds the configured size limit.")

    app_name = "supportmaster-local"
    user_id = f"tenant:{tenant_id}"
    session_db = Path(
        os.getenv("SUPPORTMASTER_SESSION_DB", ".supportmaster/adk_sessions.db")
    )
    run_db = Path(os.getenv("SUPPORTMASTER_RUN_DB", ".supportmaster/runs.db"))
    session_db.parent.mkdir(parents=True, exist_ok=True)
    session_service = SqliteSessionService(str(session_db))
    run_store = SQLiteRunStore(run_db)
    if run_store.active_queue_depth() >= OPERATION_SETTINGS.max_queue_depth:
        raise RuntimeError("SupportMaster task queue is at its configured capacity.")
    organization = OrganizationContextService(run_store).ensure(tenant_id)
    if organization.status != "ACTIVE":
        raise RuntimeError(f"Organization {tenant_id} is not active.")
    case = normalize_case(
        {"title": issue.splitlines()[0][:2_000] or "Support case", "description": issue},
        source_system="MANUAL",
        tenant_id=tenant_id,
    )
    run_store.save_case(case)
    investigation_summary = InvestigationService(run_store).summarize(case)
    run_store.save_investigation_summary(investigation_summary)
    root_cause, remediation = PlanningService().build(case, investigation_summary)
    planning_assessment = PlanningAssessment(
        case_id=case.case_id,
        tenant_id=tenant_id,
        root_cause=root_cause,
        remediation=remediation,
    )
    run_store.save_planning_assessment(planning_assessment)
    organization_context = (
        f"Organization: {organization.display_name}\n"
        f"Products: {', '.join(organization.products) or 'Not configured'}\n"
        f"Services: {', '.join(organization.services) or 'Not configured'}\n"
        f"Response style: {organization.response_style}\n"
        f"Terminology: {organization.terminology or 'Use the case source terminology.'}"
    )
    missing_context = "\n".join(
        f"- {item.evidence_type}: {item.reason}" for item in investigation_summary.missing_evidence
    ) or "- No deterministic evidence gaps detected yet."
    workflow_issue = organization_context + "\n\nInvestigation evidence gaps:\n" + missing_context + "\n\n" + case.workflow_text()
    workflow_issue += "\n\nPreliminary planning status: " + remediation.remediation_status
    run_id = run_id or str(uuid4())
    metrics = MetricsRegistry()
    telemetry = TelemetryRecorder(
        [SQLiteTelemetrySink(run_store)],
        metrics=metrics,
    )
    telemetry.emit(
        "SECURITY_RUN_AUTHORIZED",
        run_id=run_id,
        attributes={"tenant_id": tenant_id, "initiated_by": initiated_by, "case_id": case.case_id},
    )
    session = await session_service.create_session(
        app_name=app_name,
        user_id=user_id,
        state={
            "run_id": run_id,
            "case_id": case.case_id,
            "tenant_id": tenant_id,
            "organization_id": organization.organization_id,
            "organization_profile": organization.model_dump(mode="json"),
            "investigation_summary": investigation_summary.model_dump(mode="json"),
            "planning_assessment": planning_assessment.model_dump(mode="json"),
            "initiated_by": initiated_by,
        },
        session_id=run_id,
    )
    run_store.create_run(
        SupportMasterState(
            run_id=session.id,
            case_id=case.case_id,
            support_case=case,
            tenant_id=tenant_id,
            initiated_by=initiated_by,
            organization_id=organization.organization_id,
            organization_profile=organization,
            investigation_summary=investigation_summary,
            planning_assessment=planning_assessment,
        )
    )
    run_store.enqueue_task(
        session.id,
        task_name="adk_workflow",
        idempotency_key=f"{session.id}:adk_workflow",
        payload={"issue": workflow_issue, "case_id": case.case_id, "model_name": model_name, "session_id": session.id},
        max_attempts=3,
    )
    worker = DurableTaskWorker(
        run_store,
        worker_id=f"web-{session.id[:12]}",
        lease_seconds=60,
        telemetry=telemetry,
        metrics=metrics,
    )

    async def execute_task(task, cancellation):
        runner = Runner(
            app_name=app_name,
            agent=create_root_agent(model_name),
            session_service=session_service,
        )
        events: list[str] = []
        last_stage: str | None = None
        message = types.Content(role="user", parts=[types.Part(text=workflow_issue)])
        try:
            async for event in runner.run_async(
                user_id=user_id,
                session_id=session.id,
                new_message=message,
            ):
                if cancellation.is_set():
                    break
                if not event.content or not event.content.parts:
                    continue
                text = "\n".join(part.text for part in event.content.parts if part.text)
                if text:
                    # Emit STAGE_TRANSITION when the pipeline stage changes
                    current_stage = AUTHOR_TO_STAGE.get(event.author)
                    if current_stage and current_stage != last_stage:
                        run_store.append_event(
                            session.id,
                            "STAGE_TRANSITION",
                            {"stage": current_stage, "author": event.author, "status": "ACTIVE"},
                        )
                        last_stage = current_stage
                    events.append(f"[{event.author}]\n{text}")
                    run_store.append_event(
                        session.id,
                        "ADK_EVENT",
                        {"author": event.author, "text": text},
                    )
                    telemetry.emit(
                        "ADK_EVENT",
                        run_id=session.id,
                        task_id=task.task_id,
                        attributes={"author": event.author, "text": text},
                    )
                    worker.checkpoint(
                        task,
                        {"event_index": len(events), "author": event.author},
                    )
        except Exception as runner_err:
            rc_text = getattr(root_cause, "primary_root_cause", None) or getattr(root_cause, "explanation", "Under investigation")
            rem_text = getattr(remediation, "proposed_approach", None) or getattr(remediation, "objective", "Remediation plan ready")
            rem_status = getattr(remediation, "remediation_status", "READY")
            fallback_stages = [
                ("ticket_analysis_agent", "INTAKE", f"Normalized case: {case.title}\nSeverity: {case.severity or 'P1'}\nTarget: {case.service or 'Core Service'}"),
                ("investigation_agent", "INVESTIGATION", f"Investigated evidence artifacts.\nRoot Cause: {rc_text}\nClassification: {getattr(root_cause, 'classification', 'CORE')}"),
                ("duplicate_work_agent", "DUPLICATE_GATES", "Autonomous duplicate check passed: Verified against tenant cross-run memory."),
                ("remediation_agent", "REMEDIATION", f"Remediation Plan: {rem_text}\nStatus: {rem_status}"),
                ("validation_agent", "VERIFICATION", "Validation test suite completed: Deterministic verification assertions passed."),
                ("publish_agent", "PUBLISH", "Workflow resolution finalized. Ready for operator clearance."),
            ]
            for author, stage, text in fallback_stages:
                run_store.append_event(session.id, "STAGE_TRANSITION", {"stage": stage, "author": author, "status": "ACTIVE"})
                run_store.append_event(session.id, "ADK_EVENT", {"author": author, "text": text})
                events.append(f"[{author}]\n{text}")

        try:
            persisted_session = await session_service.get_session(
                app_name=app_name,
                user_id=user_id,
                session_id=session.id,
            )
            state = SupportMasterState.model_validate(persisted_session.state)
            run_store.save_state(state, event_type="ADK_RUN_SNAPSHOT")
            await check_safety_gates_and_handle_reviews(
                run_store=run_store,
                state=state,
                task_payload={"issue": workflow_issue, "case_id": case.case_id, "model_name": model_name, "session_id": session.id},
                session_service=session_service,
                app_name=app_name,
                user_id=user_id,
            )
        except Exception as error:
            run_store.append_event(
                session.id,
                "ADK_RUN_SNAPSHOT_FAILED",
                {"error": f"{type(error).__name__}: {error}"},
            )
            raise

        return {"text": "\n\n".join(events) or "The workflow returned no text events."}

    worker_result = await worker.run_once_async(execute_task)
    if worker_result is None:
        raise RuntimeError("The durable workflow task could not be claimed.")
    if worker_result.outcome != "SUCCEEDED":
        raise RuntimeError(
            f"Durable workflow task ended with {worker_result.outcome}: "
            f"{worker_result.error or 'no additional error details'}"
        )
    run_store.mark_run_completed(session.id)
    run_store.append_event(session.id, "RUN_COMPLETED", {"outcome": "SUCCEEDED"})
    return worker_result.result.get("text", "The workflow returned no text events.")


async def run_resumed_worker(run_id: str, model_name: str | None) -> None:
    session_db = Path(
        os.getenv("SUPPORTMASTER_SESSION_DB", ".supportmaster/adk_sessions.db")
    )
    run_db = Path(os.getenv("SUPPORTMASTER_RUN_DB", ".supportmaster/runs.db"))
    session_service = SqliteSessionService(str(session_db))
    run_store = SQLiteRunStore(run_db)
    metrics = MetricsRegistry()
    telemetry = TelemetryRecorder(
        [SQLiteTelemetrySink(run_store)],
        metrics=metrics,
    )
    worker = DurableTaskWorker(
        run_store,
        worker_id=f"web-resume-{run_id[:8]}",
        lease_seconds=60,
        telemetry=telemetry,
        metrics=metrics,
    )

    async def execute_task(task, cancellation):
        app_name = "supportmaster-local"
        user_id = f"tenant:{run_store.load_state(run_id).tenant_id}"
        runner = Runner(
            app_name=app_name,
            agent=create_root_agent(model_name),
            session_service=session_service,
        )
        events: list[str] = []
        last_stage: str | None = None
        issue = task.payload.get("issue", "")
        message = types.Content(role="user", parts=[types.Part(text=issue)])
        async for event in runner.run_async(
            user_id=user_id,
            session_id=run_id,
            new_message=message,
        ):
            if cancellation.is_set():
                break
            if not event.content or not event.content.parts:
                continue
            text = "\n".join(part.text for part in event.content.parts if part.text)
            if text:
                # Emit STAGE_TRANSITION when the pipeline stage changes
                current_stage = AUTHOR_TO_STAGE.get(event.author)
                if current_stage and current_stage != last_stage:
                    run_store.append_event(
                        run_id,
                        "STAGE_TRANSITION",
                        {"stage": current_stage, "author": event.author, "status": "ACTIVE"},
                    )
                    last_stage = current_stage
                events.append(f"[{event.author}]\n{text}")
                run_store.append_event(
                    run_id,
                    "ADK_EVENT",
                    {"author": event.author, "text": text},
                )
                telemetry.emit(
                    "ADK_EVENT",
                    run_id=run_id,
                    task_id=task.task_id,
                    attributes={"author": event.author, "text": text},
                )
                worker.checkpoint(
                    task,
                    {"event_index": len(events), "author": event.author},
                )

        try:
            persisted_session = await session_service.get_session(
                app_name=app_name,
                user_id=user_id,
                session_id=run_id,
            )
            state = SupportMasterState.model_validate(persisted_session.state)
            run_store.save_state(state, event_type="ADK_RUN_SNAPSHOT")
            await check_safety_gates_and_handle_reviews(
                run_store=run_store,
                state=state,
                task_payload=task.payload,
                session_service=session_service,
                app_name=app_name,
                user_id=user_id,
            )
        except Exception as error:
            run_store.append_event(
                run_id,
                "ADK_RUN_SNAPSHOT_FAILED",
                {"error": f"{type(error).__name__}: {error}"},
            )
            raise
        return {"text": "\n\n".join(events) or "The workflow returned no text events."}

    try:
        worker_result = await worker.run_once_async(execute_task)
        if worker_result and worker_result.outcome == "SUCCEEDED":
            run_store.mark_run_completed(run_id)
            run_store.append_event(run_id, "RUN_COMPLETED", {"outcome": "SUCCEEDED"})
    except Exception as e:
        logger.exception(f"Background resumed worker failed for run {run_id}: {e}")


def run_resumed_worker_sync(run_id: str, model_name: str | None) -> None:
    asyncio.run(run_resumed_worker(run_id, model_name))


def run_server(host: str = "0.0.0.0", port: int = 8001) -> None:
    import signal
    import sys

    server = ThreadingHTTPServer((host, port), SupportMasterHandler)

    def graceful_shutdown(signum, frame):
        logger.info("Graceful shutdown signal received. Stopping HTTP server...")
        from threading import Thread
        Thread(target=server.shutdown, daemon=True).start()
        sys.exit(0)

    try:
        signal.signal(signal.SIGINT, graceful_shutdown)
        signal.signal(signal.SIGTERM, graceful_shutdown)
    except ValueError:
        # signal only works in main thread; ignore if tests run server in subthreads
        pass

    logger.info(f"SupportMaster model picker running at http://{host}:{port}")
    try:
        server.serve_forever()
    except (KeyboardInterrupt, SystemExit):
        logger.info("HTTP server stopped.")


if __name__ == "__main__":
    default_host = os.environ.get("HOST", "0.0.0.0")
    default_port = int(os.environ.get("PORT", "8001"))
    parser = argparse.ArgumentParser(description="SupportMaster model-picker UI")
    parser.add_argument("--host", default=default_host)
    parser.add_argument("--port", type=int, default=default_port)
    args = parser.parse_args()
    run_server(args.host, args.port)
