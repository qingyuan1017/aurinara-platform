#!/usr/bin/env bash
set -euo pipefail

stack_name=${1:?missing stack name}
environment=${2:?missing environment}
vpc_cidr=${3:?missing VPC CIDR}
database_class=${4:?missing database class}
deployment_profile=${DEPLOYMENT_PROFILE:-standard}
dns_provider=${DNS_PROVIDER:-cloudflare}
hosted_zone_id=${HOSTED_ZONE_ID:-}
ses_source_arn=${SES_SOURCE_ARN:-}
cognito_from_address=${COGNITO_FROM_ADDRESS:-}
private_api_dns_name=${PRIVATE_API_DNS_NAME:-platform.internal.${BASE_DOMAIN:-}}
alarm_email=${ALARM_EMAIL:-}
malware_scanner_url=${MALWARE_SCANNER_URL:-}
malware_scanner_token_secret_arn=${MALWARE_SCANNER_TOKEN_SECRET_ARN:-}

required=(APPLICATION_IMAGE WORKSPACE_IMAGE BASE_DOMAIN CERTIFICATE_ARN COGNITO_DOMAIN_PREFIX AURINARA_CONSUMER_VPC_CIDR EDC_CONSUMER_VPC_CIDR CLOUDFORMATION_EXECUTION_ROLE_ARN)
for name in "${required[@]}"; do [[ -n "${!name:-}" ]] || { echo "$name is required" >&2; exit 1; }; done
[[ -n "$private_api_dns_name" ]] || { echo "PRIVATE_API_DNS_NAME could not be derived" >&2; exit 1; }
[[ -z "$ses_source_arn" && -z "$cognito_from_address" ]] || \
  [[ -n "$ses_source_arn" && -n "$cognito_from_address" ]] || {
    echo "SES_SOURCE_ARN and COGNITO_FROM_ADDRESS must be supplied together" >&2
    exit 1
  }
[[ -z "$malware_scanner_url" && -z "$malware_scanner_token_secret_arn" ]] || \
  [[ -n "$malware_scanner_url" && -n "$malware_scanner_token_secret_arn" ]] || {
    echo "MALWARE_SCANNER_URL and MALWARE_SCANNER_TOKEN_SECRET_ARN must be supplied together" >&2
    exit 1
  }
[[ "$dns_provider" == cloudflare || "$dns_provider" == route53 ]] || { echo "DNS_PROVIDER must be cloudflare or route53" >&2; exit 1; }
[[ "$deployment_profile" == standard || "$deployment_profile" == budget ]] || { echo "DEPLOYMENT_PROFILE must be standard or budget" >&2; exit 1; }
[[ "$dns_provider" != route53 || -n "$hosted_zone_id" ]] || { echo "HOSTED_ZONE_ID is required for Route 53" >&2; exit 1; }

deploy() {
  local service_image=$1
  local migration_image=$2
  local workspace_image=$3
  local enabled=$4
  aws cloudformation deploy \
    --template-file packaged.yaml \
    --stack-name "$stack_name" \
    --role-arn "$CLOUDFORMATION_EXECUTION_ROLE_ARN" \
    --capabilities CAPABILITY_NAMED_IAM CAPABILITY_AUTO_EXPAND \
    --no-fail-on-empty-changeset \
    --tags aurinara:suite=aurinara aurinara:environment="$environment" aurinara:profile="$deployment_profile" \
    --parameter-overrides \
      Environment="$environment" DeploymentProfile="$deployment_profile" VpcCidr="$vpc_cidr" BaseDomain="$BASE_DOMAIN" \
      DnsProvider="$dns_provider" HostedZoneId="$hosted_zone_id" CertificateArn="$CERTIFICATE_ARN" \
      CognitoDomainPrefix="$COGNITO_DOMAIN_PREFIX" SesSourceArn="$ses_source_arn" CognitoFromAddress="$cognito_from_address" \
      ApplicationImage="$service_image" MigrationApplicationImage="$migration_image" WorkspaceImage="$workspace_image" DatabaseInstanceClass="$database_class" \
      MalwareScannerUrl="$malware_scanner_url" MalwareScannerTokenSecretArn="$malware_scanner_token_secret_arn" \
      PrivateApiDnsName="$private_api_dns_name" AlarmEmail="$alarm_email" ServicesEnabled="$enabled" \
      AurinaraConsumerVpcCidr="$AURINARA_CONSUMER_VPC_CIDR" EdcConsumerVpcCidr="$EDC_CONSUMER_VPC_CIDR"
}

current_image=$APPLICATION_IMAGE
current_workspace_image=$WORKSPACE_IMAGE
services_enabled=false
current_profile=standard
if aws cloudformation describe-stacks --stack-name "$stack_name" >/dev/null 2>&1; then
  current_image=$(aws cloudformation describe-stacks --stack-name "$stack_name" --query "Stacks[0].Parameters[?ParameterKey=='ApplicationImage'].ParameterValue" --output text)
  current_workspace_image=$(aws cloudformation describe-stacks --stack-name "$stack_name" --query "Stacks[0].Parameters[?ParameterKey=='WorkspaceImage'].ParameterValue" --output text)
  [[ -n "$current_workspace_image" && "$current_workspace_image" != None ]] || current_workspace_image=$WORKSPACE_IMAGE
  services_enabled=$(aws cloudformation describe-stacks --stack-name "$stack_name" --query "Stacks[0].Parameters[?ParameterKey=='ServicesEnabled'].ParameterValue" --output text)
  [[ "$services_enabled" == true ]] || services_enabled=false
  current_profile=$(aws cloudformation describe-stacks --stack-name "$stack_name" --query "Stacks[0].Parameters[?ParameterKey=='DeploymentProfile'].ParameterValue" --output text)
  [[ -n "$current_profile" && "$current_profile" != None ]] || current_profile=standard
fi

# A profile switch can replace the database. Drain services before the
# replacement, migrate the new database, and re-enable them in phase two.
if [[ "$deployment_profile" != "$current_profile" ]]; then
  services_enabled=false
fi

# Register the migration image while an existing service keeps its old digest.
deploy "$current_image" "$APPLICATION_IMAGE" "$current_workspace_image" "$services_enabled"
cluster=$(aws cloudformation describe-stacks --stack-name "$stack_name" --query "Stacks[0].Outputs[?OutputKey=='ClusterName'].OutputValue" --output text)
task=$(aws cloudformation describe-stacks --stack-name "$stack_name" --query "Stacks[0].Outputs[?OutputKey=='MigrationTaskDefinitionArn'].OutputValue" --output text)
subnets=$(aws cloudformation describe-stacks --stack-name "$stack_name" --query "Stacks[0].Outputs[?OutputKey=='AppSubnetIds'].OutputValue" --output text)
security_group=$(aws cloudformation describe-stacks --stack-name "$stack_name" --query "Stacks[0].Outputs[?OutputKey=='WorkerSecurityGroupId'].OutputValue" --output text)
run_arn=$(aws ecs run-task --cluster "$cluster" --task-definition "$task" --launch-type FARGATE --network-configuration "awsvpcConfiguration={subnets=[$subnets],securityGroups=[$security_group],assignPublicIp=DISABLED}" --query 'tasks[0].taskArn' --output text)
[[ "$run_arn" != None && -n "$run_arn" ]] || { echo "migration task did not start" >&2; exit 1; }
aws ecs wait tasks-stopped --cluster "$cluster" --tasks "$run_arn"
exit_code=$(aws ecs describe-tasks --cluster "$cluster" --tasks "$run_arn" --query 'tasks[0].containers[0].exitCode' --output text)
[[ "$exit_code" == 0 ]] || { echo "migration failed with exit code $exit_code" >&2; exit 1; }
deploy "$APPLICATION_IMAGE" "$APPLICATION_IMAGE" "$WORKSPACE_IMAGE" true
