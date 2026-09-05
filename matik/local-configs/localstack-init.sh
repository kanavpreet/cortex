#!/bin/bash
set -e

echo "Creating SQS queues for local development..."

# Create Enricher DLQ
awslocal sqs create-queue --queue-name matik-enricher-dlq
echo "Enricher DLQ created."

# Get DLQ ARN
DLQ_ARN=$(awslocal sqs get-queue-attributes \
  --queue-url http://localhost:4566/000000000000/matik-enricher-dlq \
  --attribute-names QueueArn \
  --query Attributes.QueueArn \
  --output text)
echo "DLQ ARN: ${DLQ_ARN}"

# Create enricher main queue with redrive policy (maxReceiveCount=3)
awslocal sqs create-queue \
  --queue-name matik-enricher \
  --attributes "{\"VisibilityTimeout\":\"300\",\"RedrivePolicy\":\"{\\\"deadLetterTargetArn\\\":\\\"${DLQ_ARN}\\\",\\\"maxReceiveCount\\\":\\\"3\\\"}\"}"
echo "Enricher queue created with DLQ redrive policy (maxReceiveCount=3)."

# Create Scribe priority queues with DLQ redrive (maxReceiveCount=5)
awslocal sqs create-queue --queue-name matik-scribe-high-priority-dlq
SCRIBE_HIGH_DLQ_ARN=$(awslocal sqs get-queue-attributes \
  --queue-url http://localhost:4566/000000000000/matik-scribe-high-priority-dlq \
  --attribute-names QueueArn --query Attributes.QueueArn --output text)
awslocal sqs create-queue \
  --queue-name matik-scribe-high-priority \
  --attributes "{\"VisibilityTimeout\":\"300\",\"RedrivePolicy\":\"{\\\"deadLetterTargetArn\\\":\\\"${SCRIBE_HIGH_DLQ_ARN}\\\",\\\"maxReceiveCount\\\":\\\"5\\\"}\"}"
echo "Scribe high-priority queue created."

awslocal sqs create-queue --queue-name matik-scribe-medium-priority-dlq
SCRIBE_MEDIUM_DLQ_ARN=$(awslocal sqs get-queue-attributes \
  --queue-url http://localhost:4566/000000000000/matik-scribe-medium-priority-dlq \
  --attribute-names QueueArn --query Attributes.QueueArn --output text)
awslocal sqs create-queue \
  --queue-name matik-scribe-medium-priority \
  --attributes "{\"VisibilityTimeout\":\"300\",\"RedrivePolicy\":\"{\\\"deadLetterTargetArn\\\":\\\"${SCRIBE_MEDIUM_DLQ_ARN}\\\",\\\"maxReceiveCount\\\":\\\"5\\\"}\"}"
echo "Scribe medium-priority queue created."

awslocal sqs create-queue --queue-name matik-scribe-low-priority-dlq
SCRIBE_LOW_DLQ_ARN=$(awslocal sqs get-queue-attributes \
  --queue-url http://localhost:4566/000000000000/matik-scribe-low-priority-dlq \
  --attribute-names QueueArn --query Attributes.QueueArn --output text)
awslocal sqs create-queue \
  --queue-name matik-scribe-low-priority \
  --attributes "{\"VisibilityTimeout\":\"300\",\"RedrivePolicy\":\"{\\\"deadLetterTargetArn\\\":\\\"${SCRIBE_LOW_DLQ_ARN}\\\",\\\"maxReceiveCount\\\":\\\"5\\\"}\"}"
echo "Scribe low-priority queue created."

# Create enigmatologist queue
awslocal sqs create-queue --queue-name matik-enigmatologist
echo "Enigmatologist queue created."

echo "All SQS queues created:"
awslocal sqs list-queues

echo "Enricher queue attributes:"
awslocal sqs get-queue-attributes \
  --queue-url http://localhost:4566/000000000000/matik-enricher \
  --attribute-names VisibilityTimeout RedrivePolicy ApproximateNumberOfMessages
