#!/bin/bash

# AWS Resource Cleanup Script for Bedrock AgentCore Gateway
# This script provides an easy way to clean up all AWS resources

set -e

echo "🧹 AWS AgentCore Gateway Cleanup Script"
echo "========================================"
echo ""

# Check if virtual environment exists and activate it
if [ -d "venv" ]; then
    echo "📦 Activating virtual environment..."
    source venv/bin/activate
    echo "✅ Virtual environment activated"
else
    echo "⚠️  Virtual environment not found. Make sure you have the required dependencies installed."
    echo "💡 Try running: python -m pip install boto3 bedrock-agentcore-starter-toolkit"
    echo ""
fi

# Check if boto3 is available
python -c "import boto3" 2>/dev/null
if [ $? -ne 0 ]; then
    echo "❌ boto3 not found. Please install dependencies:"
    echo "   pip install boto3 bedrock-agentcore-starter-toolkit"
    echo ""
    exit 1
fi

# Check if user wants to do a dry run first
echo "Options:"
echo "1) Dry run (show what would be deleted)"
echo "2) Full cleanup (actually delete resources)"
echo "3) Cancel"
echo ""
read -p "Choose an option (1-3): " choice

case $choice in
    1)
        echo ""
        echo "🔍 Running dry run to show what would be deleted..."
        python cleanup_aws_resources.py --dry-run
        echo ""
        echo "💡 To perform the actual cleanup, run option 2 or:"
        echo "   python cleanup_aws_resources.py"
        ;;
    2)
        echo ""
        read -p "⚠️  This will permanently delete AWS resources. Are you sure? (yes/no): " confirm
        if [ "$confirm" = "yes" ]; then
            echo ""
            echo "🗑️  Performing full cleanup..."
            python cleanup_aws_resources.py
        else
            echo "❌ Cleanup cancelled."
        fi
        ;;
    3)
        echo "❌ Cleanup cancelled."
        exit 0
        ;;
    *)
        echo "❌ Invalid option. Please choose 1, 2, or 3."
        exit 1
        ;;
esac

echo ""
echo "✅ Cleanup script completed!"
