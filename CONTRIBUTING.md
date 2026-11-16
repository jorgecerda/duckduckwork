# Contributing to DuckDuckWork

Thank you for your interest in contributing to DuckDuckWork. This guide covers setup and guidelines for contributing to the codebase.

---

## Development Setup

### Prerequisites

- **Python**: 3.11 or higher
- **Git**: Installed and configured

### Clone and Install

```bash
git clone https://github.com/jorgecerda/duckduckwork.git
cd duckduckwork
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

---

## Running the Application Locally

Start the local server daemon:

```bash
APPLYPILOT_DIR=.data PYTHONPATH=src python3 src/applypilot/server.py
```

Access the application in your browser:
- **Dashboard**: `http://localhost:5050`
- **Admin Cockpit**: `http://localhost:5050/admin`

---

## Codebase Guidelines

1. **Zero Emojis**: Maintain zero emojis across all code files, templates, CLI outputs, and documentation.
2. **Clean Layout & Decoupled State**: Keep DOM state clean, enforce clear separation between navigation tabs, drawer contexts, and modals.
3. **No Hardcoded Secrets**: Never commit API keys, passwords, or personal user data. Use `.env` or dynamic configuration files.
4. **Anti-Caching**: Serve dynamic routes with anti-caching HTTP headers (`Cache-Control: no-cache, no-store, must-revalidate`).

---

## License

By contributing to DuckDuckWork, you agree that your contributions will be licensed under the project's **MIT** license.
