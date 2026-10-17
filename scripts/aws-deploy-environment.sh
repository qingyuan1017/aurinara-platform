#!/usr/bin/env bash
set -euo pipefail

stack_name=${1:?missing stack name}
environment=${2:?missing environment}
vpc_cidr=${3:?missing VPC CIDR}
database_class=${4:?missing database class}
dns_provider=${DNS_PROVIDER:-cloudflare}
hosted_zone_id=${HOSTED_ZONE_ID:-}

required=(APPLICATION_IMAGE BASE_DOMAIN CERTIFICATE_ARN COGNITO_DOMAIN_PREFIX SES_SOURCE_ARN COGNITO_FROM_ADDRESS MALWARE_SCANNER_URL MALWARE_SCANNER_TOKEN_SECRET_ARN PRIVATE_API_DNS_NAME AURINARA_CONSUMER_VPC_CIDR EDC_CONSUMER_VPC_CIDR ALARM_EMAIL CLOUDFORMATION_EXECUTION_ROLE_ARN)
for name in "${required[@]}"; do [[ -n "${!name:-}" ]] || { echo "$name is required" >&2; exit 1; }; done
[[ "$dns_provider" == cloudflare || "$dns_provider" == route53 ]] || { echo "DNS_PROVIDER must be cloudflare or route53" >&2; exit 1; }
[[ "$dns_provider" != route53 || -n "$hosted_zone_id" ]] || { echo "HOSTED_ZONE_ID is required for Route 53" >&2; exit 1; }

deploy() {
  local service_image=$1
  local migration_image=$2
  local enabled=$3
  aws cloudformation deploy \
    --template-file packaged.yaml \
    --stack-name "$stack_name" \
    --role-arn "$CLOUDFORMATION_EXECUTION_ROLE_ARN" \
    --capabilities CAPABILITY_NAMED_IAM CAPABILITY_AUTO_EXPAND \
    --no-fail-on-empty-changeset \
    --parameter-overrides \
      Environment="$environment" VpcCidr="$vpc_cidr" BaseDomain="$BASE_DOMAIN" \
      DnsProvider="$dns_provider" HostedZoneId="$hosted_zone_id" CertificateArn="$CERTIFICATE_ARN" \
      CognitoDomainPrefix="$COGNITO_DOMAIN_PREFIX" SesSourceArn="$SES_SOURCE_ARN" CognitoFromAddress="$COGNITO_FROM_ADDRESS" \
      ApplicationImage="$service_image" MigrationApplicationImage="$migration_image" DatabaseInstanceClass="$database_class" \
      MalwareScannerUrl="$MALWARE_SCANNER_URL" MalwareScannerTokenSecretArn="$MALWARE_SCANNER_TOKEN_SECRET_ARN" \
      PrivateApiDnsName="$PRIVATE_API_DNS_NAME" AlarmEmail="$ALARM_EMAIL" ServicesEnabled="$enabled" \
      AurinaraConsumerVpcCidr="$AURINARA_CONSUMER_VPC_CIDR" EdcConsumerVpcCidr="$EDC_CONSUMER_VPC_CIDR"
}

current_image=$APPLICATION_IMAGE
services_enabled=false
if aws cloudformation describe-stacks --stack-name "$stack_name" >/dev/null 2>&1; then
  current_image=$(aws cloudformation describe-stacks --stack-name "$stack_name" --query "Stacks[0].Parameters[?ParameterKey=='ApplicationImage'].ParameterValue" --output text)
  services_enabled=true
fi

# Register the migration image while an existing service keeps its old digest.
deploy "$current_image" "$APPLICATION_IMAGE" "$services_enabled"
cluster=$(aws cloudformation describe-stacks --stack-name "$stack_name" --query "Stacks[0].Outputs[?OutputKey=='ClusterName'].OutputValue" --output text)
task=$(aws cloudformation describe-stacks --stack-name "$stack_name" --query "Stacks[0].Outputs[?OutputKey=='MigrationTaskDefinitionArn'].OutputValue" --output text)
subnets=$(aws cloudformation describe-stacks --stack-name "$stack_name" --query "Stacks[0].Outputs[?OutputKey=='AppSubnetIds'].OutputValue" --output text)
security_group=$(aws cloudformation describe-stacks --stack-name "$stack_name" --query "Stacks[0].Outputs[?OutputKey=='WorkerSecurityGroupId'].OutputValue" --output text)
run_arn=$(aws ecs run-task --cluster "$cluster" --task-definition "$task" --launch-type FARGATE --network-configuration "awsvpcConfiguration={subnets=[$subnets],securityGroups=[$security_group],assignPublicIp=DISABLED}" --query 'tasks[0].taskArn' --output text)
[[ "$run_arn" != None && -n "$run_arn" ]] || { echo "migration task did not start" >&2; exit 1; }
aws ecs wait tasks-stopped --cluster "$cluster" --tasks "$run_arn"
exit_code=$(aws ecs describe-tasks --cluster "$cluster" --tasks "$run_arn" --query 'tasks[0].containers[0].exitCode' --output text)
[[ "$exit_code" == 0 ]] || { echo "migration failed with exit code $exit_code" >&2; exit 1; }
deploy "$APPLICATION_IMAGE" "$APPLICATION_IMAGE" true
