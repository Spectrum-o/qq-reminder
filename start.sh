#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

# ── 1. venv + dependencies ──────────────────────────────────────────
if [ ! -d ".venv" ] || [ ! -f ".venv/bin/activate" ]; then
    rm -rf .venv
    echo ">>> Creating virtual environment..."
    if ! python3 -m venv .venv; then
        echo "ERROR: Failed to create venv. Try: sudo apt install python3-venv"
        exit 1
    fi
fi

source .venv/bin/activate

if ! python -c "import nonebot" 2>/dev/null; then
    echo ">>> Installing dependencies..."
    pip install -e .
fi

# ── 2. Interactive .env generation (first run only) ─────────────────
if [ ! -f ".env" ]; then
    echo ">>> .env not found, let's create one."
    echo ""

    while true; do
        read -rp "Your QQ number (required): " qq
        if [ -n "$qq" ]; then
            break
        fi
        echo "QQ number cannot be empty, please try again."
    done

    read -rp "LLM API base URL (enter to skip): " llm_base
    read -rp "LLM API Key      (enter to skip): " llm_key
    read -rp "LLM model name   (enter to skip): " llm_model

    cat > .env <<EOF
DRIVER=~fastapi
HOST=0.0.0.0
PORT=8080
SUPERUSERS=["${qq}"]
COMMAND_START=["/"]
COMMAND_SEP=["."]
OWNER_QQ=${qq}
EOF

    [ -n "$llm_base" ]  && echo "LLM_API_BASE=${llm_base}" >> .env
    [ -n "$llm_key" ]   && echo "LLM_API_KEY=${llm_key}"   >> .env
    [ -n "$llm_model" ] && echo "LLM_MODEL=${llm_model}"   >> .env

    echo ""
    echo ">>> .env created."
fi

# ── 3. Start bot ────────────────────────────────────────────────────
echo ">>> Starting bot..."
python bot.py
