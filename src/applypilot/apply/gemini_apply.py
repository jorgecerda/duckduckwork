import base64
import json
import os
import time
from pathlib import Path

import httpx
from playwright.sync_api import sync_playwright
from rich.console import Console

from applypilot import config
from applypilot.database import get_connection

console = Console()

def get_gemini_action(prompt: str, screenshot_b64: str) -> dict:
    """Send screenshot and prompt to Gemini API and parse JSON response."""
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        # Load from config/env
        config.load_env()
        api_key = os.environ.get("GEMINI_API_KEY")
        
    if not api_key:
        raise ValueError("GEMINI_API_KEY is not set in your environment.")

    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={api_key}"
    
    payload = {
        "contents": [
            {
                "parts": [
                    {"text": prompt},
                    {
                        "inlineData": {
                            "mimeType": "image/png",
                            "data": screenshot_b64
                        }
                    }
                ]
            }
        ],
        "generationConfig": {
            "responseMimeType": "application/json"
        }
    }

    try:
        response = httpx.post(url, json=payload, timeout=60.0)
        response.raise_for_status()
        res_json = response.json()
        
        # Parse output text
        text_content = res_json["candidates"][0]["content"]["parts"][0]["text"]
        return json.loads(text_content.strip())
    except Exception as e:
        console.print(f"[red]Error calling Gemini API:[/red] {e}")
        if 'response' in locals() and response is not None:
            console.print(f"[dim]Response body: {response.text}[/dim]")
        return {"action": "wait", "seconds": 3, "reason": "Failed to communicate with API, retrying..."}


def run_gemini_apply(target_url: str, min_score: int = 7) -> bool:
    """Run visual auto-apply process for a specific job URL."""
    # Bootstrap environment
    config.load_env()
    conn = get_connection()
    
    # 1. Fetch job from DB
    like = f"%{target_url.split('?')[0].rstrip('/')}%"
    row = conn.execute("""
        SELECT url, title, site, application_url, tailored_resume_path,
               fit_score, location, full_description
        FROM jobs
        WHERE (url = ? OR application_url = ? OR application_url LIKE ? OR url LIKE ?)
          AND tailored_resume_path IS NOT NULL
        LIMIT 1
    """, (target_url, target_url, like, like)).fetchone()

    if not row:
        console.print(f"[red]No job found in database with URL {target_url} having a tailored resume.[/red]")
        return False

    job = dict(row)
    resume_path_txt = Path(job["tailored_resume_path"])
    resume_path_pdf = resume_path_txt.with_suffix(".pdf")
    
    if not resume_path_pdf.exists():
        console.print(f"[red]Tailored PDF resume not found at {resume_path_pdf}[/red]")
        return False

    # Load candidate profile
    profile = config.load_profile()
    
    # Read tailored resume text for prompt
    resume_text = resume_path_txt.read_text(encoding="utf-8") if resume_path_txt.exists() else ""

    # Build EEO & Profile facts
    facts = {
        "candidate_name": profile.get("contact", {}).get("name", "Candidate Name"),
        "email": profile.get("contact", {}).get("email", "candidate@example.com"),
        "phone": profile.get("contact", {}).get("phone", "+000 0000-0000"),
        "location": profile.get("contact", {}).get("location", "Remote"),
        "work_authorization": profile.get("work_authorization", "Authorized to work"),
        "sponsorship_required": profile.get("sponsorship_required", "No"),
        "salary_expectation": profile.get("salary_expectation", "Negotiable"),
        "years_experience": profile.get("years_experience", "5+ years"),
        "education": profile.get("education", "Bachelor's Degree"),
        "languages": profile.get("languages", "English"),
        "databases": profile.get("databases", ""),
        "frameworks": profile.get("frameworks", ""),
        "tools": profile.get("tools", ""),
        "resume_pdf_file_path": str(resume_path_pdf.resolve())
    }

    # Mark job as in progress
    conn.execute("UPDATE jobs SET apply_status = 'in_progress' WHERE url = ?", (job["url"],))
    conn.commit()

    console.print("\n[bold green]Starting Gemini Visual Auto-Apply Agent[/bold green]")
    console.print(f"Job: [bold]{job['title']}[/bold] | URL: {job['url']}")
    console.print(f"Tailored Resume: [dim]{resume_path_pdf}[/dim]\n")

    # Launch Playwright
    with sync_playwright() as p:
        console.print("[dim]Launching Chromium on macOS...[/dim]")
        # Launch headful (visible) browser so user can see it and solve captcha if needed
        browser = p.chromium.launch(headless=False)
        context = browser.new_context()
        page = context.new_page()
        page.set_viewport_size({"width": 1280, "height": 800})
        
        console.print(f"Navigating to job page: {job['url']}")
        page.goto(job["url"])
        try:
            page.wait_for_load_state("networkidle", timeout=5000)
        except Exception:
            pass
        
        step_limit = 40
        step = 0
        success = False
        
        while step < step_limit:
            step += 1
            console.print(f"\n[bold]Step {step}/{step_limit}[/bold]")
            
            # Wait a moment for layout to settle
            time.sleep(2)
            
            # Take screenshot
            screenshot_bytes = page.screenshot(type="png")
            screenshot_b64 = base64.b64encode(screenshot_bytes).decode("utf-8")
            
            # Formulate Gemini prompt
            system_prompt = f"""You are an autonomous job application agent. Your goal is to apply to the job on the screen for the candidate.
Current URL: {page.url}

=== CANDIDATE FACTS ===
{json.dumps(facts, indent=2)}

=== CANDIDATE RESUME TEXT ===
{resume_text}

=== AVAILABLE ACTIONS ===
Choose EXACTLY one of the following JSON actions based on the current page:
1. {{"action": "click", "selector": "CSS_OR_PLAYWRIGHT_SELECTOR", "reason": "Explanation"}}
   - Use to click links, buttons, radio options, checkboxes, or tabs.
   - You can use Playwright text selectors: `text='Easy Apply'`, `button:has-text('Next')`, `button:has-text('Submit Application')`.
2. {{"action": "fill", "selector": "CSS_OR_PLAYWRIGHT_SELECTOR", "value": "text to enter", "reason": "Explanation"}}
   - Use to type into text input fields.
3. {{"action": "upload", "selector": "CSS_OR_PLAYWRIGHT_SELECTOR", "file_path": "path", "reason": "Explanation"}}
   - Use to upload files. For resume, ALWAYS upload the path specified in facts: "{facts['resume_pdf_file_path']}"
4. {{"action": "select_option", "selector": "CSS_OR_PLAYWRIGHT_SELECTOR", "label": "option label text", "reason": "Explanation"}}
   - Use to select a value in a dropdown (<select>) element.
5. {{"action": "wait", "seconds": 3, "reason": "Explanation"}}
   - Use to wait for loading or animations to finish.
6. {{"action": "user_action_required", "reason": "Please log in to your account / complete the CAPTCHA challenge in the browser."}}
   - Use this when there is a login screen (e.g. LinkedIn Login button), password request, email verification code, or active security CAPTCHA that requires the user's manual action.
7. {{"action": "done", "status": "success", "reason": "Explanation"}}
   - Use when the application has been successfully submitted (e.g. you see a "thank you" or "application submitted" confirmation screen).
8. {{"action": "done", "status": "failed", "reason": "Explanation"}}
   - Use if you hit an unresolvable error (expired job, not eligible location, etc.) and must stop.

=== GUIDELINES ===
- Check if logged in: If you see a "Sign in" or "Log in" screen, immediately output `user_action_required` to let the user log in.
- Easy Apply: If on LinkedIn, look for the 'Easy Apply' button and click it first.
- Fill all fields: Match fields (name, email, phone, salary, experience, work auth, etc.) precisely against the Candidate Facts.
- EEO/Demographics: Choose "Decline to self-identify", "Prefer not to say", or similar options for race, gender, veteran, and disability questions.
- Output ONLY the JSON block. Do not add markdown backticks (like ```json) or any conversational text.
"""

            console.print("Querying Gemini for next action...")
            action = get_gemini_action(system_prompt, screenshot_b64)
            console.print(f"Gemini Action: [bold cyan]{json.dumps(action)}[/bold cyan]")
            
            act_type = action.get("action")
            
            if act_type == "done":
                status = action.get("status")
                reason = action.get("reason", "")
                if status == "success":
                    console.print(f"\n[bold green]Success![/bold green] {reason}")
                    conn.execute("UPDATE jobs SET apply_status = 'applied', applied_at = datetime('now') WHERE url = ?", (job["url"],))
                    conn.commit()
                    success = True
                else:
                    console.print(f"\n[bold red]Failed:[/bold red] {reason}")
                    conn.execute("UPDATE jobs SET apply_status = 'failed', apply_error = ? WHERE url = ?", (reason, job["url"]))
                    conn.commit()
                break
                
            elif act_type == "user_action_required":
                reason = action.get("reason", "Action required.")
                console.print(f"\n[bold yellow][USER ACTION REQUIRED][/bold yellow] {reason}")
                input("Complete the action in the browser window, then press [Enter] here to resume...")
                
            elif act_type == "click":
                selector = action.get("selector")
                console.print(f"Clicking selector: '{selector}'")
                try:
                    page.click(selector, timeout=8000)
                except Exception as e:
                    console.print(f"[yellow]Click failed:[/yellow] {e}. Trying scroll into view...")
                    try:
                        page.locator(selector).scroll_into_view_if_needed()
                        page.click(selector, timeout=5000)
                    except Exception as ex:
                        console.print(f"[red]Click failed completely:[/red] {ex}")
                        
            elif act_type == "fill":
                selector = action.get("selector")
                value = action.get("value")
                console.print(f"Filling selector: '{selector}' with '{value}'")
                try:
                    page.fill(selector, str(value), timeout=8000)
                except Exception as e:
                    console.print(f"[red]Fill failed:[/red] {e}")
                    
            elif act_type == "upload":
                selector = action.get("selector")
                file_path = action.get("file_path")
                console.print(f"Uploading file: '{file_path}' to selector '{selector}'")
                try:
                    page.set_input_files(selector, file_path, timeout=8000)
                except Exception as e:
                    console.print(f"[red]Upload failed:[/red] {e}")
                    
            elif act_type == "select_option":
                selector = action.get("selector")
                label = action.get("label")
                console.print(f"Selecting option: '{label}' in selector '{selector}'")
                try:
                    page.select_option(selector, label=label, timeout=8000)
                except Exception as e:
                    console.print(f"[red]Select option failed:[/red] {e}")
                    
            elif act_type == "wait":
                seconds = action.get("seconds", 3)
                console.print(f"Waiting {seconds} seconds...")
                time.sleep(seconds)
                
            else:
                console.print(f"[yellow]Unknown action type: '{act_type}'. Waiting 3 seconds...[/yellow]")
                time.sleep(3)

        # Close browser
        context.close()
        browser.close()
        
    return success
