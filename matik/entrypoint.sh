#!/bin/sh
set -e

echo "Starting Matik entrypoint..."

# Source environment file if it exists
if [ -f /app/.env ]; then
  echo "Sourcing environment variables from /app/.env"
  set -a
  . /app/.env
  set +a
  echo "Environment variables loaded successfully"
else
  echo "Warning: /app/.env not found, skipping env file sourcing"
fi

# Process config files with envsubst to replace ${VAR} with actual values
# Read from /config (read-only mount) and write to /app/config (writable)
if [ -d /config ]; then
  echo "Processing config files from /config to /app/config..."

  # Create writable config directory
  mkdir -p /app/config

  # Process each YAML file
  for config_file in /config/*.yaml /config/*.yml; do
    if [ -f "$config_file" ]; then
      filename=$(basename "$config_file")
      echo "Processing: $filename"

      # Process config with envsubst and write to writable location
      envsubst < "$config_file" > "/app/config/$filename"

      echo "Processed: $filename -> /app/config/$filename"
    fi
  done
  echo "Config file processing complete - configs available in /app/config/"
else
  echo "Warning: /config directory not found, skipping config processing"
fi

echo "Starting application: $@"
# Execute the command passed to the container
exec "$@"
