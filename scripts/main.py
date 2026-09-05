"""
Matik validation scripts CLI.

This module provides a unified entry point for all validation scripts:
- validate-configs: Compare kube and local config files
- validate-env-vars: Validate environment variables in local configs
- validate-yaml-naming: Validate YAML key naming conventions
- dlq-inspector: Inspect (read-only) or redrive Matik SQS dead letter queues
- service-signature-tester: Exercise matik-api's Phase 1c HMAC signature check
- download-grafana-dashboard: Download Grafana dashboard JSON model(s) by link
"""

import sys


def print_usage() -> None:
    """Print usage information."""
    print(
        """
Matik Validation Scripts

Usage: uv run python -m scripts <command>

Commands:
  validate-configs      Compare kube and local config files for structure matching
  validate-env-vars     Validate env vars in local-configs match .env.example
  validate-yaml-naming  Validate YAML keys follow snake_case naming convention
  dlq-inspector         Inspect (read-only) or redrive Matik SQS dead letter queues
  service-signature-tester  Exercise matik-api's Phase 1c service-signature check
  download-grafana-dashboard  Download Grafana dashboard JSON model(s) to local files
  help                  Show this help message

Examples:
  uv run python -m scripts validate-configs
  uv run python -m scripts validate-env-vars
  uv run python -m scripts validate-yaml-naming
  uv run python -m scripts dlq-inspector
  uv run python -m scripts service-signature-tester
  uv run python -m scripts download-grafana-dashboard <dashboard-or-folder-url>

Alternatively, run each script directly:
  uv run python -m validate_configs
  uv run python -m validate_env_vars
  uv run python -m validate_yaml_naming
  uv run python -m dlq_inspector
  uv run python -m grafana_dashboard_downloader <dashboard-or-folder-url>
"""
    )


def main() -> None:
    """Main entry point for the CLI."""
    if len(sys.argv) < 2:
        print_usage()
        sys.exit(0)

    command = sys.argv[1]

    if command in ("help", "--help", "-h"):
        print_usage()
        sys.exit(0)
    elif command == "validate-configs":
        from validate_configs.validator import main as validate_configs_main

        validate_configs_main()
    elif command == "validate-env-vars":
        from validate_env_vars.validator import main as validate_env_vars_main

        validate_env_vars_main()
    elif command == "validate-yaml-naming":
        from validate_yaml_naming.validator import main as validate_yaml_naming_main

        validate_yaml_naming_main()
    elif command == "dlq-inspector":
        from dlq_inspector.inspector import main as dlq_inspector_main

        dlq_inspector_main()
    elif command == "service-signature-tester":
        from service_signature_tester.tester import (
            main as service_signature_tester_main,
        )

        sys.exit(service_signature_tester_main())
    elif command == "download-grafana-dashboard":
        from grafana_dashboard_downloader.downloader import main as download_main

        sys.exit(download_main(sys.argv[2:]))
    else:
        print(f"Unknown command: {command}")
        print_usage()
        sys.exit(1)


if __name__ == "__main__":
    main()
