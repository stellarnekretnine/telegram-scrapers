#!/usr/bin/env bash
# Render build script for Playwright and dependencies

echo "Installing Python packages..."
pip install -r requirements.txt

echo "Installing Playwright browsers..."
python -m playwright install --with-deps chromium

echo "Build completed successfully!"
