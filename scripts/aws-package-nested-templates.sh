#!/usr/bin/env bash
set -euo pipefail

template_file=${1:?missing parent template path}
bucket=${2:?missing S3 template bucket}
prefix=${3:?missing S3 key prefix}
output_file=${4:?missing packaged template path}
region=${AWS_REGION:-us-east-1}

template_dir=$(cd "$(dirname "$template_file")" && pwd)
cp "$template_file" "$output_file"

mapfile -t nested_templates < <(
  sed -n -E "s/^[[:space:]]*TemplateURL:[[:space:]]*['\"]?([^'\"[:space:]]+)['\"]?[[:space:]]*$/\1/p" "$template_file"
)

((${#nested_templates[@]} > 0)) || {
  echo "no nested TemplateURL entries found in $template_file" >&2
  exit 1
}

for nested_template in "${nested_templates[@]}"; do
  source_path="$template_dir/$nested_template"
  [[ -f "$source_path" ]] || {
    echo "nested template does not exist: $source_path" >&2
    exit 1
  }

  key="${prefix%/}/$nested_template"
  aws s3 cp "$source_path" "s3://$bucket/$key" --only-show-errors
  template_url="https://$bucket.s3.$region.amazonaws.com/$key"
  sed -i -E "s|TemplateURL:[[:space:]]*['\"]?$nested_template['\"]?|TemplateURL: $template_url|" "$output_file"
done

