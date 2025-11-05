#!/usr/bin/env bash
set -o errexit

echo "📦 Installing dependencies..."
pip install --upgrade pip
pip install python-telegram-bot==13.15
pip install -r requirements.txt

echo "🎭 Installing Playwright browsers..."
python -m playwright install chromium --with-deps

echo "✅ Build complete."
