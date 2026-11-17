# Changelog

All notable changes to DuckDuckWork will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.2.11] - 2026-11-16

### Added
- **In-App Version Update Checker & 1-Click UI Updater**:
  - Implemented backend endpoint `GET /api/system/check-update` querying GitHub releases and semantically comparing against installed version.
  - Implemented `POST /api/system/update` enabling 1-click self-updating directly from the web interface using safe `git pull origin main`.
  - Added modern responsive update banner atop the right panel: "Update Available: There is a new version of DuckDuckWork (vX.Y.Z)" with `[What's New]`, `[Update Now]`, `[Remind Me Later]`, and `[✕]` dismiss controls.
  - Repositioned top system notifications (Update Banner and Missing AI Key Banner) to be the foremost elements in the right panel above search profile tabs and the theme selector.
  - Fully integrated light and dark theme styles and responsive mobile layout with touch-friendly controls.
- **Streamlined Documentation & Reference Visuals**:
  - Restructured `README.md` into a concise, developer-friendly guide with 0 emojis.
  - Added official repository hero banner `docs/images/banner.png` matching the Open Graph visual identity of `https://ddw.crda.dev`.
  - Formally documented ApplyPilot as the internal underlying discovery and matching engine behind the public DuckDuckWork brand.
  - Added native `duckduckwork` and `ddw` console script entry points in `pyproject.toml` alongside `applypilot`.
  - Embedded 16:10 light-theme reference screenshots in README (3-step onboarding wizard, job discovery & candidate crm, job copilot & fit evaluation, and search tab roi & funnel analytics) under `docs/images/`.
  - Added streamlined 3-step installation and launch instructions.
  - Transitioned project license to the permissive MIT License in `LICENSE` and `pyproject.toml`.
  - Formally included the DuckDuckGo trademark and non-affiliation disclaimer.

## [0.2.10] - 2026-11-16

### Added
- **Missing AI API Key Dashboard Warning Banner**:
  - Automatically checks if an AI provider API key (Gemini, OpenAI, OpenRouter, Groq, or Local LLM) is configured in environment variables or Admin Settings.
  - Displays a responsive alert banner at the top of the right dashboard content area when no key is configured.
  - Informs the user that job portal scraping and ingestion operate normally, while AI match scoring and tailored CV generation require an API key.
  - Provides a direct link to Settings (`/admin#db`) that dynamically retains active session prefixes (`/s/<session_id>`).
  - Added user dismissal controls: "Remind Me Later" (suppresses notification for 24 hours via `localStorage`) and a permanent Dismiss close button.
  - Optimized responsive layout for desktop and mobile viewports, including full support for light and dark themes.
- **Onboarding Skip Flow Verification**:
  - Validated end-to-end user onboarding when opting to skip API key configuration in Step 2.
  - Confirmed immediate search execution and ingestion with unscored fit badges and preserved navigation state.

## [0.2.9] - 2026-11-14

### Added
- **DuckDuckWork Analytics Dashboard & Search Tab ROI Evaluator**:
  - Implemented dedicated read-only analytics aggregation engine in `src/applypilot/analytics.py` computing real-time pipeline metrics across active databases.
  - Built high-performance REST API endpoints `GET /api/analytics` and `/s/<session_id>/api/analytics` with optional `search_id` query filtering and anti-caching headers (`Cache-Control: no-cache, no-store, must-revalidate`).
  - Added KPI summary metrics tracking total jobs discovered, saved count, applied count, archived count, pending review backlog, and average candidate-job fit score.
  - Implemented Search Tab Health and ROI Evaluator with algorithmic recommendations ("Keep Searching", "Refine Query", "Discard / Pause") driven by search volume, average score, and conversion rates.
  - Added geospatial distribution analysis with coordinate mapping across Central America (Guatemala, Nicaragua), Latin America, US Remote, and Worldwide locations.
  - Provided Top 10 portal, company, and title breakdowns with volume counts and relative percentages.
  - Integrated fit score distribution buckets (90%+, 80-89%, 70-79%, <70%, and unscored).
  - Integrated interactive analytics view in `src/applypilot/view.py` and `src/applypilot/server.py` with top navigation access and mobile-responsive layout.
  - Added comprehensive automated test suite in `tests/test_analytics.py` validating aggregation logic, search filtering, geolocation mapping, tab recommendations, and API endpoints.

### Changed
- Replaced personal chat ID in Telegram settings input placeholder with generic dummy identifier (`123456789`).
- Sanitized internal changelog documentation links from absolute local filesystem paths to relative repository paths.
- Added explicit Trademark and Non-Affiliation disclaimer regarding DuckDuckGo in `README.md`.

## [0.2.8] - 2026-11-12

### Added
- **Dual Portal URLs in Push Notifications (Home Wi-Fi + Remote 5G)**:
  - Enhanced Telegram and WhatsApp alert message templates to deliver both the local Wi-Fi LAN address (`http://<local_ip>:5050`) and the external Cloudflare Tunnel URL (`https://<tunnel>.trycloudflare.com`).
  - Allows mobile users to choose between ultra-fast local Wi-Fi browsing while at home and reliable remote access over 5G/cellular data when on the go.
  - Decoupled `get_local_wifi_portal_url()` and `get_remote_portal_url()` in `src/applypilot/notifications.py` to simultaneously construct both access links without mutual exclusion.
  - Updated test alert utilities and unit tests (`test_dispatch_job_alert_dual_urls`) ensuring both link blocks render accurately across notification payloads.

## [0.2.7] - 2026-11-11

### Added
- **Desktop 1-Click Launchers with Lid-Closed Keep Awake**:
  - Created paired macOS Desktop shortcuts: `Start DDW.command` and `Stop DDW.command`.
  - `Start DDW`: boots local server, launches Cloudflare Tunnel, activates background auto-runner, and prevents MacBook sleep even with the lid closed (`sudo pmset -a disablesleep 1` + `caffeinate`).
  - `Stop DDW`: cleanly stops the server, shuts down Cloudflare Tunnel, and restores normal macOS power management (`sudo pmset -a disablesleep 0`).
  - Added paired terminal commands `ddw` (start) and `ddw-stop` (stop) to `~/.zshrc`.
- **Dual Recency & Pipeline Job Counts in Push Notifications**:
  - Enhanced Telegram and WhatsApp push notifications to report both newly discovered roles and the total backlog of unreviewed active jobs awaiting review.
  - Formatted alert template: `Found X new jobs for <search>. You have a total of Y new jobs.`
  - Dynamically calculates active pending jobs (`is_archived = 0 AND is_saved = 0 AND applied_at IS NULL`) across all searches or per specific search profile.
  - Added unit test `test_dispatch_job_alert_with_total_pending_count` to ensure notification accuracy.

## [0.2.6] - 2026-11-10

### Added
- **Remote Mobile Access via Cloudflare Tunnel**:
  - Installed and integrated standalone `cloudflared` binary to tunnel local port `5050` through Cloudflare's global edge network via secure HTTPS (`trycloudflare.com`).
  - Added `server_external_url` configuration field in Admin Cockpit under `Environment & Server Control` with live persistence in `admin_config.json`.
  - Push notifications sent to Telegram now dynamically embed the secure Cloudflare tunnel URL so users can tap and open the portal directly from their phone anywhere in the world on 5G / 4G cellular data.
- **WhatsApp Push Notifications via CallMeBot Gateway**:
  - Implemented zero-cost WhatsApp push notification delivery in `src/applypilot/notifications.py` using the free CallMeBot gateway API.
  - Enables simultaneous dual-channel notifications: users can run both Telegram and WhatsApp alerts concurrently, or toggle either channel independently.
  - Sends direct local Wi-Fi link (`http://<local_ip>:5050`) allowing instant 1-tap portal opening directly from mobile WhatsApp messages without entering URLs manually.
  - Added Admin Cockpit controls in `/admin#db` with dedicated WhatsApp Push Alerts card, live activation switch, phone number validation, CallMeBot API key persistence, and a real-time "Send Test Alert" button.
  - Added unit test suite in `tests/test_notifications.py` verifying phone number sanitization, CallMeBot API error handling, Wi-Fi link generation, and dual-channel alert dispatching.

## [0.2.5] - 2026-11-10

### Added
- **Telegram Push Notifications for Automated Background Discovery**:
  - Implemented standalone notification engine in `src/applypilot/notifications.py` using standard library HTTP client (`urllib.request`) to dispatch rich HTML job alert messages via the official Telegram Bot API.
  - Automatically dispatches alerts when background automated searches discover new matching jobs with a streamlined format reporting exact count and direct Wi-Fi local network link (`http://<local_ip>:5050`) for immediate 1-tap mobile opening.
  - Configurable minimum fit score filter (`0%`, `60%+`, `70%+`, `80%+`, `90%+`) allowing users to filter notifications to high-match opportunities only.
  - Added REST API endpoints `GET /api/settings/notifications`, `POST /api/settings/notifications`, and `POST /api/settings/notifications/test` for credential persistence and instant live delivery verification.
  - Designed responsive "Telegram Push Notifications" settings card in the Admin Cockpit (`/admin#db`) under `Automation & Notifications` with live toggle, token/chat ID configuration, and a real-time "Send Test Alert" action button.
  - Added unit test suite `tests/test_notifications.py` covering message validation, API delivery, HTTP error handling, fit score filtering, and test notification execution.

## [0.2.4] - 2026-11-10

### Fixed
- **Zero-Flicker SSR Hydration for Auto Search Toggle**:
  - Dynamically read `admin_config.json` and active scheduler state during `generate_dashboard()` in `src/applypilot/view.py` to bake the exact interval badge (e.g. `30m`), toggle checkbox state (`checked`), and status text (`On` / `Off`) directly into the server-rendered HTML.
  - Eliminated the initial hydration flicker where the toggle momentarily displayed default `1h` before client JavaScript fetched settings.
- **Eliminated Client Timestamp Overwrite & Synchronized Profile Recency**:
  - Fixed a client bug in `src/applypilot/static/js/dashboard.js` where `data.status === 'complete'` in `pollRunnerStatus()` erroneously reset `profile.last_run = Date.now()` on every routine page load, causing recency to flash to "just now".
  - Updated `GET /api/search/status` in `src/applypilot/server.py` to provide authoritative `profile_last_runs` from `searches.yaml`.
  - Client state now syncs with server timestamps without overwriting true historical execution times.
- **Live Per-Tab Recency Ticker & Focus Resynchronization**:
  - Increased the live recency ticker in `dashboard.js` to run every 10 seconds, maintaining a smooth, live timer without requiring manual page reloads.
  - Added `visibilitychange` and `window.focus` event listeners to instantly recalculate relative recency and refresh status whenever the user navigates back to the window or tab.

## [0.2.3] - 2026-11-10

### Added
- **Smart Overdue Execution for Auto Search Scheduler**:
  - Implemented `calculate_auto_run_next_run()` in `src/applypilot/server.py` to evaluate the actual recency of existing search profiles against the configured interval.
  - When Auto Search is enabled or its interval is modified, if any search profile has never run or is older than the chosen interval (e.g. 38 minutes > 30 minutes), the scheduler immediately triggers execution within 5 seconds instead of waiting for a full initial timer countdown.
  - Added unit tests `test_calculate_auto_run_next_run_immediate_when_overdue` and `test_calculate_auto_run_next_run_future_when_recent` in `tests/test_auto_run_scheduler.py`.

## [0.2.2] - 2026-11-10

### Added
- **Per-Tab Search Updated Relative Timestamps**:
  - Added `#search-updated-timestamp` above `#main-panel-title` in `src/applypilot/view.py`, displaying real-time human-readable relative execution recency (e.g. `Search Updated 15 minutes ago`, `Search Updated 30 minutes ago`, `Search Updated 1 hour ago`, `Search Updated 2 hours ago`, or `Search not run yet`).
  - Added `format_search_updated_text()` in Python (`src/applypilot/view.py`) for zero-flicker server-rendered initial HTML and `formatSearchUpdatedText()` in JavaScript (`src/applypilot/static/js/dashboard.js`) for dynamic client-side updates.
  - Implemented `update_profile_last_run()` in `src/applypilot/config.py`, automatically updated upon discovery completion for each search profile in `src/applypilot/server.py`.
  - Added dynamic 30-second interval refresh in `dashboard.js` and instant timestamp recalculation on tab switching and background runner completion.
  - Added unit test suite `tests/test_search_updated_timestamp.py` testing interval formatting, configuration persistence, and HTML dashboard rendering.

## [0.2.1] - 2026-11-10

### Added
- **Automated Background Search Scheduler**:
  - Implemented background daemon loop `_auto_run_scheduler_daemon()` and startup hook `start_auto_run_scheduler()` in `src/applypilot/server.py` to trigger full discovery runs across all active search profiles automatically at configured intervals.
  - Added REST API endpoints `GET /api/settings/auto-run` and `POST /api/settings/auto-run` supporting configurable intervals: `15m` (every 15 mins), `30m` (every 30 mins), `1h` (every 1 hour, default), and `2h` (every 2 hours).
  - Integrated settings persistence into `admin_config.json` with next scheduled execution time calculation and telemetry tracking.
  - Added "Automated Search Schedule" configuration card inside the Database & Maintenance section (`/admin#db`) with interval selector, toggle switch, live status badge, and next scheduled execution timestamp.
- **Sidebar 3-Button Control Grid**:
  - Relocated the Auto Search toggle control from the top navigation bar to the primary sidebar directly adjacent to the Run All button.
  - Established a clean, ergonomic 3-button sidebar layout:
    - **Row 1**: `Run All` | `Auto Search: On/Off` (with configured interval pill and instant toggle switch)
    - **Row 2**: `Settings` (full-width button)
  - Added automated test suite `tests/test_auto_run_scheduler.py` verifying interval mapping, configuration persistence, and scheduler state reporting.

## [0.2.0] - 2026-11-10

### Added
- **Option C Hybrid Cloud & Local-First Open-Source Architecture**:
  - Added dual-mode runtime (`is_cloud_mode()`, `get_session_quota()`, `update_session_quota()` in `src/applypilot/server.py`) distinguishing unrestricted Local Mode (`localhost:5050`) from Cloud Sandbox Mode (`DUCKDUCKWORK_CLOUD_MODE=1` / `VERCEL=1` on `https://duckduckwork.com`).
  - Enforced zero-cost cloud sandbox quotas (2 free searches per session, +1 instant bonus search upon joining the Pro Early Access waitlist, up to 2 search tabs, and `results_per_site` capped at 12) while unlocking unlimited cloud searches when a user connects their own free API key (BYOK).
- **DuckDuckWork Pro Early Access Waitlist & Quota Gate Modal**:
  - Added `render_pro_waitlist_modal()` (`#pro-waitlist-modal`) in `src/applypilot/view.py` and sidebar trigger button (`#sidebar-pro-btn`).
  - Automatically displays when a cloud user reaches the free search or tab limit, offering 3 paths:
    1. Join the **DuckDuckWork Pro Early Access Waitlist** (`POST /api/waitlist/join`) to unlock +1 instant bonus search.
    2. Connect a free **Bring Your Own Key (BYOK)** API key (Google AI Studio or OpenRouter `:free`) for unlimited browser searches.
    3. Download and run the **100% Free Local Open-Source** repository on GitHub.
- **Neon PostgreSQL Waitlist & Product Demand Telemetry**:
  - Added `duckduckwork_waitlist` and `duckduckwork_telemetry` tables in `init_postgres_db()` (`src/applypilot/server.py`) with automatic local JSON fallback (`.data/waitlist_telemetry.json`).
  - Added a **Pro Waitlist & Demand** tab (`#tab-btn-growth` / `#panel-growth`) in the Admin Cockpit (`/admin`) displaying live conversion metrics (`Waitlist Signups`, `Quota Gate Views`, `BYOK Connected`, `GitHub Repo Clicks`, `Cloud Searches Run`), a table of captured waitlist leads, and 1-click CSV export (`exportWaitlistCsv()`).
- **Zero-Cost Resource Conservation (Batch LLM Scoring, OpenRouter Free Tier, & Direct-First Proxy Pool)**:
  - Implemented `score_jobs_batch()` and `_parse_batch_score_response()` in `src/applypilot/scoring/scorer.py` to evaluate up to 8 job postings in a single LLM request in Cloud Mode, reducing API request consumption by up to 8x.
  - Added first-class `OPENROUTER_API_KEY` and `:free` model routing (`meta-llama/llama-3.3-70b-instruct:free`) with `HTTP-Referer: https://duckduckwork.com` and `X-Title: DuckDuckWork` headers in `src/applypilot/llm.py`.
  - Upgraded `resolve_webshare_proxy()` and `_scrape_with_retry()` in `src/applypilot/discovery/jobspy.py` to rotate across all 10 Webshare.io proxies and support Direct-First scraping with automatic proxy fallback on HTTP `429`/`403` to conserve the 1 GB/month free proxy bandwidth.
  - Added unit test suite `tests/test_option_c_hybrid_cloud.py` covering batch scoring, quota state transitions, waitlist persistence, OpenRouter detection, and UI rendering.

## [0.1.18] - 2026-11-09

### Added
- **Hire With Near Native Job Board Integration**:
  - Integrated Hire With Near (`https://jobs.hirewithnear.com`) as a native, independent job source for remote Latin American talent working with US companies.
  - Implemented `fetch_hirewithnear_jobs()` in `src/applypilot/discovery/providers/latam_providers.py` consuming Hire With Near's public REST API (`/api/jobs/list`) with query parameterization, pagination, datePosted timestamp recency filtering (24h/48h), salary parsing (both structured ranges and regex fallbacks), liveness checks, and deduplication via `scan_history`.
  - Added "Hire With Near" checkbox to the Search Profile Manager modal (`#form-site-hirewithnear`) allowing profiles to include or exclude Hire With Near individually.
  - Added Hire With Near source filter toggle and live job counter badge to the dashboard sidebar.
  - Added Hire With Near toggle switch to Admin Settings (`/admin`) for platform-wide source configuration.
  - Added comprehensive automated unit tests in `tests/test_fase3.py` (`TestHireWithNearProvider`) testing API parsing, database insertion, salary extraction, deduplication, location rejection, and error resilience.

## [0.1.17] - 2026-11-09

### Removed
- **GitHub Actions Automated CI Workflow**:
  - Removed `.github/workflows/ci.yml` to prevent automated remote test runs and failure notifications on push to private repository.
  - Automated testing remains fully available locally via `python -m unittest discover tests`.

## [0.1.16] - 2026-11-09

### Fixed
- **Synchronized Search Profile Tab Badges with Active Score Filters**:
  - Resolved an issue where search profile tabs displayed positive unreviewed job counts (e.g. `13` or `9`) despite all visible jobs having been reviewed and archived.
  - Updated `updateSearchTabBadges()` in `src/applypilot/static/js/dashboard.js` to filter unreviewed job counts against each search profile's configured `score_filters`, preventing low-match jobs hidden by default filters from inflating the tab badge.
  - Updated server-side rendering (SSR) in `src/applypilot/view.py` (`profile_new_counts`) to respect profile `score_filters`, ensuring accurate zero-state badge presentation on initial page loads and browser refreshes.
- **GitHub Actions CI Ruff Linting & Deprecation Warnings**:
  - Fixed 15 Ruff linting errors across `src/` and `tests/` (including unused imports, ambiguous variable names, unused local variables, and redundant f-strings) that caused GitHub Action step 5 (`ruff check src/`) to exit with code 1.
  - Configured `fail-fast: false` on the CI test matrix in `.github/workflows/ci.yml` so Python 3.11 and 3.12 jobs evaluate independently.

## [0.1.15] - 2026-11-09

### Added
- **Score Filter & Match Visibility in Search Profile Manager**:
  - Added a dedicated "Score Filter & Match Visibility" section to the create/edit search profile modal (`#search-manager-modal`).
  - Users can select which score tiers to list on the dashboard (`High Match (85%+)`, `Solid Match (70–84%)`, `Moderate Match (50–69%)`, `Low Match (<50%)`).
  - Default configuration automatically enables High, Solid, and Moderate matches while hiding Low Match (<50%) postings from the card list, sidebar tallies, and SSR view.
  - Persisted `score_filters` across search profile configurations in `searches.yaml`.
- **AI-Powered Two-Line Job Role Summaries**:
  - Replaced heuristic description extraction with concise, plain-English 2-line AI summaries generated directly by Gemini.
  - Summaries explain what the role does and its core objective in simple terms, avoiding recruiter fluff, buzzwords, and company boilerplate.
  - Added schema migration for `ai_summary` column in `jobs` SQLite database and integrated AI summary generation into `scorer.py` and `view.py`.
- **Dedicated Project Gemini API Key**:
  - Connected and configured the new dedicated Google Gemini API key across environment settings and admin configurations.

## [0.1.14] - 2026-11-09

### Fixed
- **Eliminated Flash of Hidden Job Cards on Refresh**:
  - Fixed server-side rendering (SSR) in `render_job_card()` to tag saved and applied cards with `.hidden` alongside archived and blocked cards, preventing saved/applied/archived cards from flashing on initial page load when viewing the "New Jobs" tab.
  - Aligned SSR initial display styles for `#job-grid-container` and `#no-jobs-empty-state` based on `new_jobs_count`, ensuring the clean empty state is displayed immediately without layout shifts when zero new jobs exist.

### Changed
- **Two-Line Role Responsibilities Summary**:
  - Replaced raw description snippet slicing with an intelligent `extract_role_summary()` algorithm that strips HTML and filters company marketing intros ("About Us", "We are leading...", etc.), isolating 1–2 concise sentences focused on core role responsibilities and duties.
  - Applied CSS `-webkit-line-clamp: 2` to `.job-desc-snippet` for clean visual truncation.
- **Two-Line Fit Alignment Summary**:
  - Updated fit analysis presentation via `format_fit_summary()` to explicitly state candidate resume alignment (`Strongly Aligned (80-100%)`, `Moderately Aligned (60-79%)`, `Not Aligned (<60%)`, or `Pending Evaluation`) referencing match percentage.
  - Added semantic styling classes (`fit-aligned`, `fit-moderate`, `fit-unaligned`, `fit-pending`) with light and dark mode accent badges.
  - Refined LLM scoring prompt in `scorer.py` to instruct future AI evaluations to return 2 concise sentences directly stating whether the candidate's resume is aligned or not with the role based on match percentage.

## [0.1.13] - 2026-11-06

### Fixed
- **Synchronized Admin Favicon with Dashboard**:
  - Aligned all favicon, shortcut icon, apple-touch-icon, and theme-color link tags on the Admin Cockpit (`/admin`) to match the primary candidate dashboard.
  - Generated multi-resolution binary favicon icons (`favicon.ico` with 16x16, 32x32, 48x48, 64x64 layers, `favicon-32x32.png`, and `favicon-16x16.png`) derived directly from the official DuckDuckWork logo.
  - Standardized favicon and apple-touch-icon link declarations across all application HTML views (`generate_admin_html`, `generate_dashboard`, `generate_onboarding_html`, `generate_deleted_session_html`, and `generate_maintenance_html`).

## [0.1.12] - 2026-11-06

### Added
- **Blocked Job Posters Field in Search Manager Modal**:
  - Added a dedicated "Blocked Job Posters (comma-separated)" input field in the Search Profile create/edit modal (`#search-manager-modal`).
  - Users can configure specific company names or job poster identifiers (e.g. `BAIRESDEV`, `CyberCoders`) to automatically exclude unwanted hiring agencies and job posters.
  - Form state binds seamlessly across create, edit, and save operations in `dashboard.js` and persists across session configurations.
  - Automatically applied the `BairesDev` block across all existing searches and session search profiles.

### Changed
- **Dynamic Job Poster Filtering & Non-Destructive Data Persistence**:
  - Filtered blocked poster job cards dynamically from the dashboard DOM, tab count badges, and sidebar metrics without executing destructive database purges or SQL deletes.
  - Scraper integration in `jobspy.py` filters out blocked companies at ingestion time during both single-profile runs and full crawls.

## [0.1.11] - 2026-11-03

### Fixed
- **Synchronized Search Tab Count Badges with Sidebar**:
  - Bound per-tab new job count badges directly to real-time candidate card state (`!isArchived && !isApplied && !isSaved`) calculated from live DOM cards and database records.
  - Added real-time badge synchronization when archiving individual jobs, archiving all jobs via `triggerArchiveAllJobs`, saving jobs, or marking jobs applied.
  - Tab badges now immediately decrement in lockstep with the sidebar's `New Jobs` counter, transitioning to `0` (neutral badge) when all jobs in a search tab are archived.
  - Server-side template rendering in `view.py` now embeds computed initial count badges directly into `<button class="search-tab-btn">`, eliminating layout shifts and initial stale counts.
  - Optimistic UI state updates ensure archiving actions update both sidebar counters and tab badges instantaneously.

## [0.1.10] - 2026-11-03

### Fixed
- **HTTP 429 Rate-Limit Mitigation & Gemini Model Upgrade**:
  - Upgraded Google Gemini default model from sunset `gemini-2.0-flash` / daily-restricted `gemini-3.8-flash` (20 requests/day cap) to `gemini-3.5-flash-lite`, offering full free-tier quota and low latency.
  - Implemented thread-safe inter-request rate limiting (`_throttle_llm_call(4.0)`) enforcing a 4.0-second delay between Gemini API calls to strictly respect the 15 RPM free-tier threshold.
  - Replaced unthrottled concurrent burst workers with adaptive pacing (`max_workers=2` for Gemini) preventing burst 429 rejections.
  - Fixed scoring failure persistence: failed LLM calls no longer overwrite database jobs with `fit_score=0` and raw error messages (`Fit Analysis: ... Status/429`), leaving them unscored (`NULL`) for seamless subsequent retries.
  - Added UI defensive filter in `render_job_card` ensuring error URLs and HTTP status codes are never rendered in the fit analysis reasoning pill.
  - Cleaned up existing database records corrupted with HTTP 429 error messages.

### Changed
- **Incremental Tab Release During Multi-Search Runs**:
  - The sequential runner now releases each search profile immediately upon completion (discovery + scoring) rather than waiting for all queued profiles to finish.
  - Incremental `generate_dashboard()` generation updates on-disk HTML as each profile finishes, and background polling dynamically injects fresh job cards into `#job-grid-container` without interrupting the user or forcing a disruptive full page reload.
  - The animated loading spinner and status banner remain active only on the profile tab actively running in the background, allowing candidates to freely review and inspect completed tabs while subsequent campaigns run.

## [0.1.9] - 2026-11-03

### Added
- **Sidebar 'Run All Searches' Button (Task #35)**:
  - Added a prominent 'Run All Searches' action button in the left sidebar positioned directly above the Settings button.
  - Implemented sequential multi-profile execution queue (`Option 1`) traversing all active search profiles one by one to avoid rate limits, job portal IP bans, and Gemini API quota exhaustion.
  - Live execution updates shifting active tab indicators, status banner messages, and progress counters through each sequential profile phase.
- **Per-Tab New Job Count Badges (Task #35)**:
  - Added dynamic count badges positioned before each tab name (`[+5] Scrum Master` for new roles, `0 Project Manager` when no new roles were discovered).
  - Badges are updated both during sequential multi-profile execution and individual tab searches.
  - Badges persist locally across page refreshes and session navigation via `localStorage` synchronization.
  - Implemented complete theme styling with emerald badges for new jobs, neutral badges for zero counts, and high-contrast dark theme overrides.

## [0.1.8] - 2026-11-02

### Added
- **Animated Loading Indicator in Active Search Tab (Task #32)**:
  - Added an animated loading spinner inside the top search profile tab button that is actively executing a background scraping or scoring run.
  - Provides instant visual feedback on active tasks even when switching between tabs: inactive running tabs display a subtle primary background tint and rotating spinner.
  - Implemented accessibility compliance with dynamic `aria-busy` toggling and `prefers-reduced-motion` CSS overrides.
- **Incremental AI Fit Scoring Progress Counter & Progress Bar (Task #34)**:
  - Updated `run_scoring` with thread-safe `progress_callback` reporting live completion across concurrent worker threads.
  - Runner state and status banner now display real-time incremental progress (e.g. `Scoring job 1 of 10 with Gemini... [10%]`) instead of a static message.
  - Added a dedicated horizontal progress bar widget to the runner status banner with smooth CSS transitions and dark theme styling.

## [0.1.7] - 2026-10-17

### Added
- **Instant AI API Key Testing on Paste (Onboarding Step 2)**:
  - Added real-time testing when an API key is pasted into the AI Engine step.
  - Displays dynamic verification status feedback (latency in ms, green checkmark badge) or explicit error diagnostics.
  - Automatically verifies credentials and ensures a valid connection before allowing the user to proceed to Step 3.

### Changed
- **Compact Viewport Optimization (100vh Fit)**:
  - Streamlined copy and trimmed vertical spacing across all three onboarding steps.
  - Ensured all cards, forms, and action buttons fit comfortably inside both desktop and mobile viewports without requiring vertical page scrolling.
- **Step 3 Background & Skills Question Removal**:
  - Removed the candidate background and key skills textarea from Step 3, as candidate qualifications are automatically extracted from the uploaded PDF resume.

## [0.1.6] - 2026-10-16

### Added
- **Onboarding Step 3 CV / Resume Upload (PDF)**:
  - Added an optional CV upload drop-zone in Step 3 of the onboarding wizard.
  - Automatically parses PDF text, creates the candidate search profile, attaches the file as `resumes/search_1.pdf`, and uses it to calibrate initial AI fit scoring.
  - Clear user guidance explaining that uploading a CV enhances match scoring from the first search and can be edited anytime.
- **Admin AI Quota & Usage Monitor**:
  - Added a real-time AI usage and rate-limit gauge in `/admin` tracking daily request consumption against provider free tiers (e.g. 15 RPM / 1,500 RPD for Google Gemini).
  - Shows rate limits, daily quotas, estimated costs, and live connection latency.
- **Show/Hide Key Toggle & Key Persistence in Admin**:
  - Added a password visibility toggle to the AI API Key input in the Admin panel.
  - Pre-populates the configured API key from local storage upon page load and allows immediate connection testing.
  - Added fallback in `/api/llm/test` so connection testing works seamlessly with stored credentials.

### Changed
- **Removed Deprecated Getting Started Card**:
  - Removed the obsolete "Configure your AI Engine to get started" card from the dashboard skeleton loader.
- **Anonymized Form Examples & Test Fixtures**:
  - Replaced all personal data placeholders and test fixtures across the application and documentation with generic examples (Alex Morgan, United States, generic test companies and universities).
- **Added Task #30 to Backlog**:
  - Documented strategic path for hosted cloud SaaS deployment and dual-tier architecture (Free Local OSS vs Managed Cloud SaaS) in `BACKLOG.md`.

## [0.1.5] - 2026-10-16

### Added
- **3-Step Welcome Onboarding for New Users**:
  - Implemented an elegant 3-step onboarding wizard for first-time users with a progress stepper:
    - Step 1: Concise overview of DuckDuckWork with discovery, AI scoring, and local privacy feature highlights.
    - Step 2: Local AI engine connection with multi-provider support (Gemini, OpenAI, OpenRouter, Groq), show/hide key toggle, and clear local storage privacy guarantee.
    - Step 3: First job search creation with role title, country/worldwide location picker, work mode selector, and optional candidate background summary.
  - Added `/api/onboarding/setup` POST endpoint that securely saves API configuration locally, initializes database and candidate profile, creates the initial search in `searches.yaml`, and starts background job discovery.
  - Redirects immediately upon launch to the active dashboard with live crawling and scoring indicators.
  - Fully responsive on mobile viewports with zero horizontal overflow and clean stacked controls.
  - Strictly preserved existing user database and configuration via `is_new_user()` detection.

## [0.1.4] - 2026-10-04

### Fixed
- **Environment Variable Precedence & Safari GET Ping Fixes**:
  - Configured `load_admin_config()` in `src/applypilot/server.py` so server environment variables (`GEMINI_API_KEY`, `WEBSHARE_API_KEY`) strictly override stale JSON config keys.
  - Added GET route support for `/api/admin/proxy/test` in `src/applypilot/server.py` and simplified client `fetch()` in `src/applypilot/view.py` to fix Safari `SyntaxError` on proxy ping calls.

## [0.1.3] - 2026-10-04

### Fixed
- **Read-Only Server Environment Security Mode (Option A)**:
  - Updated **API Keys** tab in `/admin` to display Google Gemini AI Key and Webshare REST API Key in secure **Read-Only Status Cards** obtained from server environment variables (`GEMINI_API_KEY`, `WEBSHARE_API_KEY`, `APPLYPILOT_PROXY`).
  - Fixed Gemini client fallback in `src/applypilot/llm.py` to automatically route requests via native Gemini API `v1beta` `generateContent` when Google OpenAI compat layer returns 404/403.
  - Sanitized HTTP header strings in Safari (`X-Admin-Token`, `X-CSRF-Token`) to prevent client-side `SyntaxError` crashes.

## [0.1.2] - 2026-10-04

### Changed
- **Unified API Keys Admin Tab**:
  - Consolidated Google Gemini AI API Key and Webshare REST API Key management into a single, clean **API Keys** tab in `/admin`.
  - Added `/api/admin/gemini` POST endpoint to allow updating and saving the Google Gemini API Key directly from the UI.
  - Reorganized admin navigation: `Active Sessions`, `API Keys`, `Sources & Job Boards`, `Execution Logs`, `Config & Password`.

## [0.1.1] - 2026-10-04

### Added
- **Webshare REST API v2 Integration (Task 20)**:
  - Added support for Webshare REST API Key authentication (`WEBSHARE_API_KEY` / `webshare_api_key`).
  - Automated retrieval of active direct HTTP/SOCKS5 proxies from Webshare API (`GET /api/v2/proxy/list/`).
  - Added bandwidth consumption tracking (MB used / 1 GB free tier limit) and request metrics in `/admin`.
  - Added `/api/admin/proxy` and `/api/admin/proxy/test` endpoints with live latency and IP ping verification.

## [0.1.0] - 2026-10-03

### Added
- **Multi-Source Job Discovery**:
  - Integrated job scrapers for LinkedIn, Indeed, Get on Board (Tech & LATAM startup positions), Remotive, and WeWorkRemotely.
  - Multi-profile search query management via YAML configuration (`searches.yaml`).
- **AI Fit Scoring & Resume Tailoring**:
  - Multi-provider LLM support for Google Gemini (`gemini-2.0-flash`, `gemini-2.5-flash`), OpenAI (`gpt-4o-mini`), and local models (Ollama/llama.cpp).
  - Automated candidate-job fit rating (1–100%) with detailed reasoning breakdown.
  - Custom resume variation generator and tailored cover letter drafting without factual fabrication.
- **Instant CRM Web Dashboard (`/`)**:
  - Multi-tab search profile management with drag-and-drop tab reordering.
  - Full-width role search bar integrated into tab header for desktop and mobile responsiveness.
  - Interactive filtering by score match tier, country, work mode (Remote/Hybrid/On-site), company, and pipeline status (`New`, `Saved`, `Applied`, `Archived`).
  - Slide-out right position drawer displaying enriched job descriptions, company logo fallbacks, quick action buttons, custom candidate notes, and CRM tracking stages.
- **Admin Control Cockpit (`/admin`)**:
  - Real-time session monitoring with live status detection (**Online**, **Scraping...**, **Scoring...**, **Idle**).
  - Real-time metrics cards for *Total Sessions*, *Online Sessions*, *Scanned Jobs Breakdown*, *AI Status*, and *Disk Storage*.
  - Maintenance Mode toggle displaying a custom light-theme HTTP 530 screen while maintaining `/admin` access.
  - Live scraper source toggles for LinkedIn, Indeed, Get on Board, Remotive, and WeWorkRemotely.
  - AI Health Ping test to measure API connection latency and model responsiveness.
  - Live execution log viewer terminal.
- **Vercel Serverless Function Support & Cloud Session Persistence**:
  - Added `vercel.json`, `api/index.py`, and `requirements.txt` for serverless deployment on Vercel.
  - Added native cloud session persistence layer in `server.py` supporting Supabase, PostgreSQL, Vercel Storage (KV/Blob), and environment variable payload auto-restoration.
  - Implemented standalone `scripts/sync_session.py` for direct HTTP session migration and Base64 environment payload generation without exposing personal data in version control.
- **Local-First & Privacy Boundaries**:
  - Data persistence in local SQLite (`applypilot.db`) and JSON configuration files inside `.data/`, fully protected by `.gitignore`.

