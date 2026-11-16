# DuckDuckWork Project & Agent Guidelines

## Repository Hygiene & Documentation Protocol (Pristine Standard)
- **Strict English Documentation**: All documentation files (`.md`), comments, docstrings, commit messages, and repository artifacts MUST be written exclusively in 100% professional English.
- **Synchronized Feature Documentation**: Every new feature, bug fix, patch, layout update, or infrastructure change MUST be immediately documented in both `CHANGELOG.md` and `README.md`.
- **Strict Semantic Version Control**: Maintain disciplined version bumping (`MAJOR.MINOR.PATCH`) in `pyproject.toml` and `src/applypilot/__init__.py` for every release, patch, or feature addition.
- **Brand Consistency**: The sole public brand name is **DuckDuckWork**. `ApplyPilot` and `JobSpy` are strictly internal underlying engines/modules and must only be referenced as such in `README.md` technical architecture sections.

## UI & Visual Verification Protocol
- **Visual Capture Requirement**: After every UI modification, layout update, CSS change, or visual bug fix, ALWAYS capture a fresh screenshot of the rendered portal via headless Chrome and display/embed the screenshot in the response to the user.
- **Mobile Compatibility**: The user frequently views the portal from their mobile device. Ensure all captures, previews, and responsive layouts look pristine on mobile viewports as well as desktop viewports.

## Core Product Architecture & Purpose
- **Persistent Web Job Portal**: DuckDuckWork is a persistent cloud job search engine and candidate management portal. Sessions (`/s/<session_id>`), searches, job lead statuses (`new`, `saved`, `applied`, `archived`), notes, and AI fit scores MUST persist permanently in cloud database storage (Supabase / PostgreSQL / Cloud DB) across all web traffic. Users and admins can continuously create, view, update, and delete sessions over time.
- **Zero Personal Data in Version Control**: Production cloud databases house active session data, while the Git repository MUST remain 100% free of personal resume data, personal credentials, or seed database files.

## Codebase Principles (Ponytail Philosophy)
- **Zero Bloat & Zero Emojis**: Maintain 0 emojis across all templates, CLI outputs, log outputs, and source code files.
- **Native Platform & Decoupled State**: Keep DOM state clean, avoid unnecessary dependencies, and enforce clear separation between top navigation tabs, sidebar accordion controls, and modal dialog contexts.
- **Anti-Caching HTTP Headers**: Always serve dynamic dashboard content with anti-caching HTTP headers (`Cache-Control: no-cache, no-store, must-revalidate`) to prevent stale browser state in Safari and mobile clients.

## Database & Session Persistence Protocol
- **Strict Decoupling of Data & UI/Admin Layers**: Code deployments, UI updates, and admin panel metric lookups MUST NEVER execute destructive operations, purges, or automatic deletions on user databases (`applypilot.db`) or session directories. Data persistence and UI/functionality logic are strictly decoupled layers.
- **Read-Only Admin Metric Collection**: Admin data functions (`get_admin_data()`) MUST be strictly read-only and must never invoke destructive file system or SQL `DELETE` calls during routine page loads or metric retrieval.
- **Zero Hardcoded Session Rules**: The codebase MUST NOT contain hardcoded session IDs or hardcoded session rules. Session states (e.g. permanent status) are driven dynamically by configuration and user actions.
- **Explicit Administrative Control**: Session state changes, purging, or deletion must strictly occur only upon explicit user/admin user-triggered actions or explicit maintenance commands.

## Search & Recency Protocol
- **Initial Search Recency Window**: The initial onboarding search for a new query uses a base window of 24 hours (`hours_old: 24`). If 24 hours yields 0 results, the automatic fallback recency window expands to a STRICT MAXIMUM of 48 hours (`hours_old: 48`). Initial searches MUST NOT exceed 48 hours under any circumstances.

## User Consultation & Non-Autonomous Decision Protocol
- **Zero Autonomous Architectural/Business Decisions**: The agent MUST NEVER autonomously decide or modify business parameters, time limits, recency thresholds, search fallback ranges, or user-facing behavior.
- **Mandatory Option Presentation**: Whenever a trade-off, architectural choice, parameter adjustment, or design decision arises, the agent MUST present clear options and ask the user to choose before implementing any change.

## Local Iteration & Zero Unrequested Git Push Protocol
- **Strict Local Iteration**: All fixes, feature updates, debugging, and verifications MUST be executed and tested 100% locally on the local environment.
- **Explicit Git Push Only**: The agent MUST NEVER run `git push` to GitHub or trigger remote deployments autonomously. Git pushes are strictly forbidden unless the user explicitly commands a push.
- **Single Commit Per Push**: When the user explicitly commands a push, all local iteration commits MUST be squashed or combined into a single unified commit before pushing, ensuring Vercel triggers strictly ONE single build deployment.



