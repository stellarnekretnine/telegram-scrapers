#!/usr/bin/env bash
# Render build script for Playwright setup

echo "Installing dependencies..."
pip install -r requirements.txt

echo "Installing Playwright browsers..."
npx playwright install chromium --with-deps || python -m playwright install chromium --with-deps

echo "✅ Playwright browser installation complete!"
