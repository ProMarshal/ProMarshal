#!/bin/bash

# ProMarshal - Force Refresh Next.js Dev Server
# This script clears cache and restarts the dev server

echo "🔄 Stopping Next.js dev server..."
pkill -f "next dev" || true

echo "🗑️  Clearing Next.js cache..."
rm -rf .next

echo "🗑️  Clearing node_modules cache..."
rm -rf node_modules/.cache

echo "✅ Cache cleared!"
echo ""
echo "🚀 Now run: npm run dev"
echo ""
echo "Then in your browser:"
echo "  - Chrome/Edge: Cmd+Shift+R (Mac) or Ctrl+Shift+R (Windows)"
echo "  - Safari: Cmd+Option+R"
echo ""
