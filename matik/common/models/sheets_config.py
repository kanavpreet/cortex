"""Google Sheets configuration for continuous production audits."""

from sqlmodel import Field, SQLModel


class SheetsConfig(SQLModel):
    """Google Sheets client configuration.

    Note: No table=True, this is a configuration model only.
    """

    spreadsheet_id: str = Field(..., description="Google Sheets spreadsheet ID")
    worksheet_name: str = Field(..., description="Worksheet (tab) name to read/write")
    row_key_column: str | None = Field(
        default=None,
        description=(
            "Header column used as the unique row key (e.g. 'reference_id'). "
            "Required for upsert_rows; not needed for append-only worksheets."
        ),
    )
    service_account_json: str | None = Field(
        default=None,
        description="Service-account credentials as a JSON string (e.g. from secret-lair)",
    )
    service_account_file: str | None = Field(
        default=None,
        description="Path to a service-account JSON key file (local dev fallback)",
    )
