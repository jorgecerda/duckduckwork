"""Telegram push notifications service for DuckDuckWork."""

import html
import json
import logging
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

log = logging.getLogger(__name__)


def send_telegram_message(
    bot_token: str,
    chat_id: str,
    text: str,
    parse_mode: str = "HTML",
    disable_web_page_preview: bool = True,
) -> tuple[bool, str]:
    """Send an HTML/Markdown formatted message via the official Telegram Bot API.

    Returns:
        (True, "Success message") or (False, "Error message")
    """
    token = (bot_token or "").strip()
    cid = (chat_id or "").strip()

    if not token:
        return False, "Telegram Bot Token is required."
    if not cid:
        return False, "Telegram Chat ID is required."

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": cid,
        "text": text,
        "parse_mode": parse_mode,
        "disable_web_page_preview": disable_web_page_preview,
    }

    req_data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=req_data,
        headers={"Content-Type": "application/json", "User-Agent": "DuckDuckWork/0.2.5"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            resp_body = resp.read().decode("utf-8")
            data = json.loads(resp_body)
            if data.get("ok"):
                return True, "Notification delivered successfully."
            return False, data.get("description", "Unknown Telegram API response.")
    except urllib.error.HTTPError as e:
        err_msg = f"HTTP {e.code}"
        try:
            err_body = e.read().decode("utf-8")
            err_json = json.loads(err_body)
            desc = err_json.get("description")
            if desc:
                err_msg = f"{err_msg}: {desc}"
        except Exception:
            pass
        return False, err_msg
    except urllib.error.URLError as e:
        return False, f"Network connection error: {e.reason}"
    except Exception as e:
        return False, f"Unexpected error: {e}"


def send_whatsapp_message(
    phone: str,
    api_key: str,
    text: str,
) -> tuple[bool, str]:
    """Send a plain text WhatsApp notification via the free CallMeBot API.

    Returns:
        (True, "Success message") or (False, "Error message")
    """
    raw_phone = (phone or "").strip().replace(" ", "").replace("-", "").replace("(", "").replace(")", "")
    if raw_phone.startswith("+"):
        raw_phone = raw_phone[1:]
    key = (api_key or "").strip()

    if not raw_phone:
        return False, "WhatsApp phone number is required (with country code, e.g. 1234567890)."
    if not key:
        return False, "CallMeBot API Key is required."

    encoded_text = urllib.parse.quote_plus(text)
    url = f"https://api.callmebot.com/whatsapp.php?phone={raw_phone}&text={encoded_text}&apikey={key}"

    req = urllib.request.Request(
        url,
        headers={"User-Agent": "DuckDuckWork/0.2.6"},
        method="GET",
    )

    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            resp_body = resp.read().decode("utf-8", errors="ignore")
            # CallMeBot returns HTML or plain text indicating status
            if "error" in resp_body.lower() or "invalid" in resp_body.lower():
                clean_err = html.unescape(resp_body).strip()
                return False, clean_err
            return True, "WhatsApp notification queued successfully."
    except urllib.error.HTTPError as e:
        return False, f"HTTP {e.code}: {e.reason}"
    except urllib.error.URLError as e:
        return False, f"Network connection error: {e.reason}"
    except Exception as e:
        return False, f"Unexpected error: {e}"


def get_local_wifi_portal_url(session_id: str = "") -> str:
    """Resolve the local Wi-Fi network URL (e.g. http://192.168.1.11:5050) accessible on LAN."""
    import os
    from applypilot.server import get_local_ip

    ip = get_local_ip()
    port = int(os.environ.get("APPLYPILOT_PORT", os.environ.get("PORT", 5050)))
    base_url = f"http://{ip}:{port}"
    if session_id and session_id not in ("default", "personal", ".data"):
        return f"{base_url}/s/{session_id}"
    return base_url


def get_remote_portal_url(session_id: str = "") -> str:
    """Resolve the remote/external URL (e.g. Cloudflare tunnel) if configured."""
    from applypilot.server import is_cloud_mode, load_admin_config

    admin_cfg = load_admin_config()
    configured_url = str(admin_cfg.get("server_external_url", "")).strip()
    if configured_url:
        base_url = configured_url.rstrip("/")
    elif is_cloud_mode():
        base_url = "https://duckduckwork.com"
    else:
        return ""

    if session_id and session_id not in ("default", "personal", ".data"):
        return f"{base_url}/s/{session_id}"
    return base_url


def get_wifi_portal_url(session_id: str = "") -> str:
    """Resolve the preferred portal URL (remote if configured, otherwise local Wi-Fi)."""
    remote = get_remote_portal_url(session_id)
    if remote:
        return remote
    return get_local_wifi_portal_url(session_id)


def send_test_whatsapp_notification(phone: str, api_key: str, session_id: str = "") -> tuple[bool, str]:
    """Send an immediate test alert to WhatsApp with Wi-Fi and remote links."""
    local_url = get_local_wifi_portal_url(session_id)
    remote_url = get_remote_portal_url(session_id)

    if remote_url and remote_url != local_url:
        links_block = (
            f"Home Wi-Fi (Faster): {local_url}\n"
            f"Remote (5G): {remote_url}"
        )
    else:
        links_block = f"Open on your phone: {local_url}"

    msg = (
        "DuckDuckWork Notification Test\n\n"
        "Your WhatsApp connection is working successfully!\n\n"
        f"{links_block}"
    )
    return send_whatsapp_message(phone, api_key, msg)


def send_test_telegram_notification(bot_token: str, chat_id: str, session_id: str = "") -> tuple[bool, str]:
    """Send an immediate test alert with both Wi-Fi and Cloudflare links to verify Telegram credentials."""
    local_url = get_local_wifi_portal_url(session_id)
    remote_url = get_remote_portal_url(session_id)

    if remote_url and remote_url != local_url:
        links_block = (
            f"<b>Home Wi-Fi (Faster):</b>\n<a href=\"{local_url}\">{local_url}</a>\n\n"
            f"<b>Remote (5G):</b>\n<a href=\"{remote_url}\">{remote_url}</a>"
        )
    else:
        links_block = f"Open on your phone:\n<a href=\"{local_url}\">{local_url}</a>"

    msg = (
        "<b>DuckDuckWork Notification Test</b>\n\n"
        "Your Telegram connection is working successfully!\n\n"
        f"{links_block}"
    )
    return send_telegram_message(bot_token, chat_id, msg)


def detect_telegram_chat_id(bot_token: str) -> tuple[str | None, str]:
    """Inspect recent bot updates to auto-detect the user chat ID."""
    token = (bot_token or "").strip()
    if not token:
        return None, "Bot Token is required."

    url = f"https://api.telegram.org/bot{token}/getUpdates"
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "DuckDuckWork/0.2.5"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            if not data.get("ok"):
                return None, data.get("description", "Failed to retrieve updates from Telegram.")
            updates = data.get("result", [])
            for u in reversed(updates):
                msg = u.get("message") or u.get("channel_post")
                if msg:
                    chat = msg.get("chat", {})
                    cid = chat.get("id")
                    if cid:
                        name = chat.get("first_name", chat.get("title", ""))
                        return str(cid), f"Detected chat ID {cid} ({name})"
            return None, "No messages found yet. Please send a message or /start to your bot in Telegram first."
    except Exception as e:
        return None, f"Error querying Telegram: {e}"


def dispatch_job_alerts(
    jobs: list[dict[str, Any]],
    search_name: str = "All Searches",
    session_id: str = "default",
    min_score: int = 0,
    server_url: str = "",
    total_pending_count: int | None = None,
    new_found_count: int | None = None,
) -> tuple[bool, str]:
    """Evaluate and dispatch formatted job alerts to active notification channels (Telegram, WhatsApp)."""
    from applypilot.server import load_admin_config, log_event

    admin_cfg = load_admin_config()
    tg_enabled = bool(admin_cfg.get("telegram_enabled", False))
    wa_enabled = bool(admin_cfg.get("whatsapp_enabled", False))

    if not tg_enabled and not wa_enabled:
        return False, "All notification channels are disabled."
    if not jobs:
        return False, "No jobs provided for notification."

    configured_min_score = int(admin_cfg.get("telegram_min_score", min_score or 0))

    # Filter jobs by minimum fit score threshold if configured
    matching_jobs = []
    for j in jobs:
        score = j.get("fit_score")
        if configured_min_score > 0:
            if score is not None and score < configured_min_score:
                continue
        matching_jobs.append(j)

    if not matching_jobs:
        log_event(
            f"Notification alert skipped: None of the {len(jobs)} new jobs met the {configured_min_score}% score threshold."
        )
        return True, "No jobs met the minimum fit score threshold."

    # Resolve local Wi-Fi and remote portal URLs for phone access
    local_url = get_local_wifi_portal_url(session_id)
    remote_url = server_url or get_remote_portal_url(session_id)

    if remote_url and remote_url != local_url:
        links_block_tg = (
            f"<b>Home Wi-Fi (Faster):</b>\n<a href=\"{local_url}\">{local_url}</a>\n\n"
            f"<b>Remote (5G):</b>\n<a href=\"{remote_url}\">{remote_url}</a>"
        )
        links_block_wa = (
            f"Home Wi-Fi (Faster):\n{local_url}\n\n"
            f"Remote (5G):\n{remote_url}"
        )
    else:
        links_block_tg = f"Open on your phone:\n<a href=\"{local_url}\">{local_url}</a>"
        links_block_wa = f"Open on your phone:\n{local_url}"

    count = new_found_count if (new_found_count is not None and new_found_count > 0) else len(matching_jobs)
    count_str = f"{count} new job{'s' if count != 1 else ''}"

    total_phrase_tg = ""
    total_phrase_wa = ""
    if total_pending_count is not None and total_pending_count > 0:
        total_noun = "new job" if total_pending_count == 1 else "new jobs"
        total_phrase_tg = f"\nYou have a total of <b>{total_pending_count} {total_noun}</b>."
        total_phrase_wa = f"\nYou have a total of {total_pending_count} {total_noun}."

    results = []

    # 1. Telegram
    if tg_enabled:
        bot_token = str(admin_cfg.get("telegram_bot_token", "")).strip()
        chat_id = str(admin_cfg.get("telegram_chat_id", "")).strip()
        if bot_token and chat_id:
            safe_search = html.escape(search_name)
            tg_message = (
                f"<b>DuckDuckWork Alert</b>\n\n"
                f"Found <b>{count_str}</b> for <b>{safe_search}</b>.{total_phrase_tg}\n\n"
                f"{links_block_tg}"
            )
            tg_ok, tg_msg = send_telegram_message(bot_token, chat_id, tg_message)
            results.append(("Telegram", tg_ok, tg_msg))
            if tg_ok:
                log_event(f"Telegram notification sent for {count_str} ({search_name}).")
            else:
                log_event(f"Telegram notification failed: {tg_msg}", level="WARN")

    # 2. WhatsApp (CallMeBot)
    if wa_enabled:
        wa_phone = str(admin_cfg.get("whatsapp_phone", "")).strip()
        wa_key = str(admin_cfg.get("whatsapp_api_key", "")).strip()
        if wa_phone and wa_key:
            wa_message = (
                f"DuckDuckWork Alert\n\n"
                f"Found {count_str} for {search_name}.{total_phrase_wa}\n\n"
                f"{links_block_wa}"
            )
            wa_ok, wa_msg = send_whatsapp_message(wa_phone, wa_key, wa_message)
            results.append(("WhatsApp", wa_ok, wa_msg))
            if wa_ok:
                log_event(f"WhatsApp notification sent for {count_str} ({search_name}).")
            else:
                log_event(f"WhatsApp notification failed: {wa_msg}", level="WARN")

    if not results:
        return False, "No notification channels configured with valid credentials."

    any_success = any(r[1] for r in results)
    summary_msg = "; ".join(f"{ch}: {'OK' if ok else err}" for ch, ok, err in results)
    return any_success, summary_msg


# Alias for backward compatibility
dispatch_telegram_job_alert = dispatch_job_alerts
