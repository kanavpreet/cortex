"""Common utility functions for the Matik platform."""

from common.utils import log_utils
from common.utils.cron_utils import duration_to_cron
from common.utils.datetime_utils import parse_timestamp_to_utc, utc_now_naive
from common.utils.db_utils import (
    build_connection_url,
    create_long_lived_engine,
    create_short_lived_engine,
    generate_rds_auth_token,
)
from common.utils.env_utils import (
    determine_environment,
    get_env_variable,
    is_local_environment,
)
from common.utils.github_utils import is_github_rate_limit_error
from common.utils.hash_utils import generate_string_hash
from common.utils.iam_utils import assume_role, get_session
from common.utils.jira_utils import (
    aggregate_comments_for_llm,
    extract_tcmr_link,
    get_first_non_empty_string_field,
)
from common.utils.model_utils import make_response_model
from common.utils.retry_utils import (
    execute_with_retry,
    fetch_all_with_retry,
    get_backoff_delay,
    is_transient_db_error,
    run_with_retry,
)
from common.utils.secret_file_utils import create_secure_temp_file

__all__ = [
    "aggregate_comments_for_llm",
    "assume_role",
    "build_connection_url",
    "create_long_lived_engine",
    "create_secure_temp_file",
    "create_short_lived_engine",
    "determine_environment",
    "duration_to_cron",
    "execute_with_retry",
    "extract_tcmr_link",
    "fetch_all_with_retry",
    "generate_rds_auth_token",
    "generate_string_hash",
    "get_backoff_delay",
    "get_env_variable",
    "get_first_non_empty_string_field",
    "get_session",
    "is_github_rate_limit_error",
    "is_local_environment",
    "is_transient_db_error",
    "log_utils",
    "make_response_model",
    "parse_timestamp_to_utc",
    "run_with_retry",
    "utc_now_naive",
]
