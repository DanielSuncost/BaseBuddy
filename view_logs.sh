#!/bin/bash
# BaseBuddy Log Viewer

LOG_DIR="./logs"

echo "========================================="
echo "  BaseBuddy Log Viewer"
echo "========================================="
echo ""

if [ ! -d "$LOG_DIR" ]; then
    echo "❌ No logs directory found. Start BaseBuddy to generate logs."
    exit 1
fi

echo "Available logs:"
echo "  1) View main application log (basebuddy.log)"
echo "  2) View errors only (errors.log)"
echo "  3) Tail main log (live)"
echo "  4) Tail errors (live)"
echo "  5) View all logs in directory"
echo "  6) Search logs for a keyword"
echo ""

read -p "Choose an option (1-6): " choice

case $choice in
    1)
        if [ -f "$LOG_DIR/basebuddy.log" ]; then
            less "$LOG_DIR/basebuddy.log"
        else
            echo "❌ basebuddy.log not found"
        fi
        ;;
    2)
        if [ -f "$LOG_DIR/errors.log" ]; then
            less "$LOG_DIR/errors.log"
        else
            echo "❌ errors.log not found"
        fi
        ;;
    3)
        if [ -f "$LOG_DIR/basebuddy.log" ]; then
            echo "📡 Live log (Ctrl+C to exit)"
            tail -f "$LOG_DIR/basebuddy.log"
        else
            echo "❌ basebuddy.log not found"
        fi
        ;;
    4)
        if [ -f "$LOG_DIR/errors.log" ]; then
            echo "📡 Live errors (Ctrl+C to exit)"
            tail -f "$LOG_DIR/errors.log"
        else
            echo "❌ errors.log not found"
        fi
        ;;
    5)
        ls -lh "$LOG_DIR"
        ;;
    6)
        read -p "Enter search keyword: " keyword
        echo "Searching for '$keyword'..."
        grep -i "$keyword" "$LOG_DIR"/*.log
        ;;
    *)
        echo "Invalid option"
        ;;
esac




