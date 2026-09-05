#!/usr/bin/env bash
# empty_dlq.sh — purge all Matik sandbox SQS queues (main + DLQs)
# Usage: ./empty_dlq.sh
# Requires: aws CLI configured with valid credentials (set AWS_PROFILE if needed)

set -euo pipefail

QUEUES=(
  # Enigmatologist
  "https://sqs.us-east-1.amazonaws.com/172631448019/matik-enig-triggers-sandbox-queue"

  # Enricher
  "https://sqs.us-east-1.amazonaws.com/172631448019/matik-enr-enrichment-sandbox-queue"
  "https://sqs.us-east-1.amazonaws.com/172631448019/matik-enr-enrichment-sandbox-dlq"

  # Scribe high-priority
  "https://sqs.us-east-1.amazonaws.com/172631448019/matik-scrb-high-sandbox-queue"
  "https://sqs.us-east-1.amazonaws.com/172631448019/matik-scrb-high-sandbox-dlq"

  # Scribe medium-priority
  "https://sqs.us-east-1.amazonaws.com/172631448019/matik-scrb-medium-sandbox-queue"
  "https://sqs.us-east-1.amazonaws.com/172631448019/matik-scrb-medium-sandbox-dlq"

  # Scribe low-priority
  "https://sqs.us-east-1.amazonaws.com/172631448019/matik-scrb-low-sandbox-queue"
  "https://sqs.us-east-1.amazonaws.com/172631448019/matik-scrb-low-sandbox-dlq"
)

echo "The following sandbox queues will be purged:"
for q in "${QUEUES[@]}"; do
  echo "  - ${q##*/}"
done

echo ""
read -r -p "Type 'yes' to continue: " confirm
[[ "$confirm" == "yes" ]] || { echo "Aborted."; exit 0; }

echo ""
for q in "${QUEUES[@]}"; do
  name="${q##*/}"
  if aws sqs purge-queue --queue-url "$q" 2>/dev/null; then
    echo "  ✓ $name"
  else
    echo "  ✗ $name (failed — queue may not exist or already purged recently)"
  fi
done

echo ""
echo "Done. Note: SQS purge can take up to 60 seconds to complete."
