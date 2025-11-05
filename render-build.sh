#!/usr/bin/env bash
# ✅ Render build script for Playwright setup

echo "Installing Python dependencies..."
pip install -r requirements.txt

echo "Installing Playwright browsers..."
python -m playwright install chromium

echo "✅ Playwright installed successfully!"
