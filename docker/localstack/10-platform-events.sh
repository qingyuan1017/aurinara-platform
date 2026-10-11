#!/bin/bash
set -euo pipefail

region="us-east-1"
account_id="000000000000"
bus="aurinara-platform"
rule="aurinara-platform-events"

if ! awslocal events describe-event-bus --name "$bus" --region "$region" >/dev/null 2>&1; then
  awslocal events create-event-bus --name "$bus" --region "$region" >/dev/null
fi
awslocal sqs create-queue --queue-name aurinara-platform-events --region "$region" >/dev/null
awslocal sqs create-queue --queue-name aurinara-edc-platform-events --region "$region" >/dev/null

aurinara_arn="arn:aws:sqs:${region}:${account_id}:aurinara-platform-events"
edc_arn="arn:aws:sqs:${region}:${account_id}:aurinara-edc-platform-events"

awslocal events put-rule \
  --name "$rule" \
  --event-bus-name "$bus" \
  --event-pattern '{"source":["aurinara.platform"]}' \
  --region "$region" >/dev/null
awslocal events put-targets \
  --event-bus-name "$bus" \
  --rule "$rule" \
  --targets \
    "Id=aurinara,Arn=${aurinara_arn}" \
    "Id=aurinara-edc,Arn=${edc_arn}" \
  --region "$region" >/dev/null
