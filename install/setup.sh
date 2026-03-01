#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────────────
#  🐾 Pawbot Installer
#  One command to install and configure pawbot.
#
#  Usage:
#    curl -fsSL https://pawbot.thecloso.com/install | bash
#    curl -fsSL https://raw.githubusercontent.com/HKUDS/pawbot/main/install/setup.sh | bash
#    bash setup.sh
# ──────────────────────────────────────────────────────────────────────
set -euo pipefail

# ── Colors ────────────────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
BOLD='\033[1m'
DIM='\033[2m'
RESET='\033[0m'

LOGO="🐾"

info()    { echo -e "${CYAN}${LOGO}${RESET} $*"; }
success() { echo -e "${GREEN}✓${RESET} $*"; }
warn()    { echo -e "${YELLOW}⚠${RESET} $*"; }
fail()    { echo -e "${RED}✗${RESET} $*"; exit 1; }

# ── Banner ────────────────────────────────────────────────────────────
echo ""
echo -e "${BOLD}${CYAN}"
echo "  ┌─────────────────────────────────────────┐"
echo "  │     🐾  Pawbot Installer                │"
echo "  │     Ultra-Lightweight AI Assistant       │"
echo "  └─────────────────────────────────────────┘"
echo -e "${RESET}"
echo ""

# ── Step 1: Check Python ≥ 3.11 ──────────────────────────────────────
info "Checking Python version..."

PYTHON=""
for cmd in python3 python; do
    if command -v "$cmd" &>/dev/null; then
        ver=$("$cmd" --version 2>&1 | grep -oP '\d+\.\d+' | head -1)
        major=$(echo "$ver" | cut -d. -f1)
        minor=$(echo "$ver" | cut -d. -f2)
        if [ "$major" -ge 3 ] && [ "$minor" -ge 11 ]; then
            PYTHON="$cmd"
            break
        fi
    fi
done

if [ -z "$PYTHON" ]; then
    fail "Python 3.11 or higher is required but not found.

  Install it:
    macOS:          brew install python@3.12
    Ubuntu/Debian:  sudo apt install python3.12 python3.12-venv
    Windows:        Download from https://python.org/downloads
    Any system:     curl https://pyenv.run | bash && pyenv install 3.12"
fi

PY_VERSION=$("$PYTHON" --version 2>&1)
success "Found $PY_VERSION"

# ── Step 2: Check for pip ─────────────────────────────────────────────
info "Checking pip..."

PIP=""
for cmd in pip3 pip; do
    if command -v "$cmd" &>/dev/null; then
        PIP="$cmd"
        break
    fi
done

if [ -z "$PIP" ]; then
    info "pip not found, trying to bootstrap..."
    "$PYTHON" -m ensurepip --upgrade 2>/dev/null || true
    PIP="$PYTHON -m pip"
fi

success "pip is available"

# ── Step 3: Detect install mode ───────────────────────────────────────
INSTALL_MODE="pip"
SCRIPT_DIR=""

# If we're inside a cloned repo (setup.sh is at install/setup.sh, pyproject.toml is at ../)
if [ -f "$(dirname "$0")/../pyproject.toml" ] 2>/dev/null; then
    REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
    if grep -q 'pawbot-ai' "$REPO_ROOT/pyproject.toml" 2>/dev/null; then
        INSTALL_MODE="dev"
        SCRIPT_DIR="$REPO_ROOT"
    fi
fi

# ── Step 4: Check for existing installation ───────────────────────────
EXISTING=""
if command -v pawbot &>/dev/null; then
    EXISTING=$(pawbot --version 2>/dev/null || echo "unknown")
    warn "Pawbot is already installed: $EXISTING"
    echo ""
    echo -e "  ${BOLD}u${RESET} = Upgrade to latest version"
    echo -e "  ${BOLD}r${RESET} = Reinstall (force)"
    echo -e "  ${BOLD}q${RESET} = Quit"
    echo ""
    read -rp "  Choose [u/r/q]: " choice
    case "$choice" in
        u|U) info "Upgrading..." ;;
        r|R) info "Reinstalling..." ;;
        *)   echo "Cancelled."; exit 0 ;;
    esac
    echo ""
fi

# ── Step 5: Install pawbot ────────────────────────────────────────────
info "Installing pawbot..."
echo ""

if [ "$INSTALL_MODE" = "dev" ]; then
    info "Installing from local repo in development mode..."
    $PIP install -e "$SCRIPT_DIR" 2>&1 | tail -5
else
    if [ -n "$EXISTING" ]; then
        $PIP install --upgrade pawbot-ai 2>&1 | tail -5
    else
        $PIP install pawbot-ai 2>&1 | tail -5
    fi
fi

echo ""

# ── Step 6: Verify installation ──────────────────────────────────────
if ! command -v pawbot &>/dev/null; then
    # Try adding common pip install paths
    export PATH="$HOME/.local/bin:$PATH"
    if ! command -v pawbot &>/dev/null; then
        warn "pawbot command not found in PATH."
        echo ""
        echo -e "  Add this to your shell config (~/.bashrc or ~/.zshrc):"
        echo -e "  ${CYAN}export PATH=\"\$HOME/.local/bin:\$PATH\"${RESET}"
        echo ""
        echo "  Then run: source ~/.bashrc"
        echo ""
        fail "Please fix your PATH and run 'pawbot onboard --setup' to continue."
    fi
fi

INSTALLED_VERSION=$(pawbot --version 2>/dev/null || echo "installed")
success "Pawbot $INSTALLED_VERSION"
echo ""

# ── Step 7: Run onboard with setup ───────────────────────────────────
info "Running first-time setup..."
echo ""

pawbot onboard --setup

echo ""
echo -e "${BOLD}${GREEN}"
echo "  ┌─────────────────────────────────────────┐"
echo "  │     🐾  Pawbot is ready!                │"
echo "  └─────────────────────────────────────────┘"
echo -e "${RESET}"
echo ""
echo -e "  ${BOLD}Quick start:${RESET}"
echo -e "    ${CYAN}pawbot agent -m \"Hello!\"${RESET}      Send a message"
echo -e "    ${CYAN}pawbot agent${RESET}                  Interactive chat"
echo -e "    ${CYAN}pawbot gateway${RESET}                Start Telegram/WhatsApp"
echo -e "    ${CYAN}pawbot status${RESET}                 Check configuration"
echo ""
echo -e "  ${DIM}Docs: https://github.com/HKUDS/pawbot${RESET}"
echo ""
