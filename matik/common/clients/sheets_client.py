"""Google Sheets client for continuous production audits.

Read/write access to a single worksheet, keyed by a configured row-key column.
This is the primary output for audit pipelines: humans can edit rows directly
in the sheet (e.g. filling in a gold entity for a row parked for review), and
the next run picks up the edit via ``read_rows``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

import gspread

from common.models.sheets_config import SheetsConfig
from common.utils import log_utils

logger = log_utils.get_logger(__name__)

# Sheets-only: open_by_key reads/writes cells directly by ID, no Drive API
# calls needed (unlike opening by title/search), so Drive scope is omitted.
_SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]


def _build_client(sheets_config: SheetsConfig) -> gspread.Client:
    if sheets_config.service_account_json:
        info = json.loads(sheets_config.service_account_json)
        return gspread.service_account_from_dict(info, scopes=_SCOPES)
    if sheets_config.service_account_file:
        return gspread.service_account(
            filename=sheets_config.service_account_file, scopes=_SCOPES
        )
    raise ValueError(
        "SheetsConfig needs either service_account_json or service_account_file"
    )


def _find_table(
    spreadsheet: gspread.Spreadsheet, sheet_id: int
) -> dict[str, Any] | None:
    """The worksheet's Table (id + row/column range), if the range has been
    converted to one.

    Plain ``values.append``/``values.update`` writes land as ordinary cells
    outside a Table's tracked range, so new rows don't inherit its column
    formatting or dropdown validation. ``AppendCellsRequest``'s ``tableId``
    field looks like the fix but isn't reliable: verified against the real
    API, a write for a table on any sheet other than the spreadsheet's first
    (sheetId 0) silently lands on sheet 0 instead. ``_append_rows`` instead
    grows this range explicitly (``UpdateTableRequest``) and writes at an
    exact, explicit ``sheetId``/row position (``UpdateCellsRequest``) — both
    keyed off this real range, never off ``tableId`` alone. ``None`` means
    the worksheet isn't a Table, so callers fall back to the plain append.
    """
    metadata = spreadsheet.fetch_sheet_metadata(
        params={"fields": "sheets(properties(sheetId),tables(tableId,range))"}
    )
    for sheet in metadata.get("sheets", []):
        if sheet["properties"].get("sheetId") != sheet_id:
            continue
        tables = sheet.get("tables") or []
        if tables:
            table: dict[str, Any] = tables[0]
            return table
    return None


def _entered_value(value: Any) -> dict[str, Any]:
    """``userEnteredValue`` for one cell, preserving real booleans as Sheets
    boolean cells (matching what plain ``values.append`` already produced
    for ``bool`` fields like ``change_related``) rather than stringifying
    them to "True"/"False"."""
    if isinstance(value, bool):
        return {"userEnteredValue": {"boolValue": value}}
    return {"userEnteredValue": {"stringValue": str(value)}}


@dataclass
class SheetsClient:
    """Client for a single worksheet, keyed by ``sheets_config.row_key_column``.

    Example usage:
        from common.config import load_config

        config = load_config("matik-audit-root-cause-config.yml")
        client = SheetsClient(sheets_config=config.sheets)

        rows = client.read_rows()
        client.upsert_rows([{"reference_id": "INC-1234", "audit_status": "scored"}])
    """

    sheets_config: SheetsConfig

    _worksheet: gspread.Worksheet = field(init=False)
    _table: dict[str, Any] | None = field(init=False, default=None)

    def __post_init__(self) -> None:
        client = _build_client(self.sheets_config)
        spreadsheet = client.open_by_key(self.sheets_config.spreadsheet_id)
        self._worksheet = spreadsheet.worksheet(self.sheets_config.worksheet_name)
        self._table = _find_table(spreadsheet, self._worksheet.id)
        logger.info(
            "created SheetsClient",
            spreadsheet_id=self.sheets_config.spreadsheet_id,
            worksheet_name=self.sheets_config.worksheet_name,
            table_id=self._table["tableId"] if self._table else None,
        )

    def _append_rows(
        self, values: list[list[Any]], existing_row_count: int | None = None
    ) -> None:
        """Append rows, staying inside the worksheet's Table (if it has one)
        so they inherit its formatting/validation instead of landing as
        plain cells beyond its tracked range.

        ``existing_row_count`` lets a caller that already knows the sheet's
        current row count (``upsert_rows`` does) skip a redundant fetch.
        """
        if self._table is None:
            self._worksheet.append_rows(values)
            return

        if existing_row_count is None:
            existing_row_count = len(self._worksheet.get_all_values())

        # 0-indexed: row 0 is the header, so the row count is already the
        # next free row's index.
        next_row_index = existing_row_count
        new_end_row_index = next_row_index + len(values)

        sheet_id = self._worksheet.id
        table_range = {**self._table["range"], "sheetId": sheet_id}
        requests: list[dict[str, Any]] = []
        if new_end_row_index > table_range["endRowIndex"]:
            table_range = {**table_range, "endRowIndex": new_end_row_index}
            requests.append(
                {
                    "updateTable": {
                        "table": {
                            "tableId": self._table["tableId"],
                            "range": table_range,
                        },
                        "fields": "range",
                    }
                }
            )
            self._table["range"] = table_range

        requests.append(
            {
                "updateCells": {
                    "rows": [
                        {"values": [_entered_value(cell) for cell in row]}
                        for row in values
                    ],
                    "fields": "userEnteredValue",
                    "start": {
                        "sheetId": sheet_id,
                        "rowIndex": next_row_index,
                        "columnIndex": table_range["startColumnIndex"],
                    },
                }
            }
        )
        self._worksheet.spreadsheet.batch_update({"requests": requests})

    def read_rows(self) -> list[dict[str, Any]]:
        """Read every row, keyed by the header row's column names."""
        records: list[dict[str, Any]] = self._worksheet.get_all_records()
        return records

    def append_rows(self, rows: list[dict[str, Any]]) -> None:
        """Append rows unconditionally, no row-key lookup.

        For worksheets with no meaningful single-row identity (e.g. a
        one-row-per-correlation detail sheet, where many rows legitimately
        share the same ``reference_id``) — ``upsert_rows``'s update-by-key
        logic would silently collapse those onto a single sheet row instead
        of appending each one.

        Raises:
            ValueError: If the worksheet has no header row.
        """
        if not rows:
            return

        header = self._worksheet.row_values(1)
        if not header:
            raise ValueError(
                "worksheet has no header row — create it before writing rows"
            )

        values = [[row.get(column, "") for column in header] for row in rows]
        self._append_rows(values)
        logger.info(
            "appended rows to sheet",
            appended=len(values),
            worksheet_name=self.sheets_config.worksheet_name,
        )

    def upsert_rows(self, rows: list[dict[str, Any]]) -> None:
        """Update existing rows (matched by ``row_key_column``) or append new ones.

        Batches all updates into one API call and all appends into another,
        rather than one call per row — Google Sheets has tight per-100-second
        rate limits, and this is called with however many incidents settled
        since the last run, not one row at a time.

        Raises:
            ValueError: If ``row_key_column`` isn't configured, the worksheet
                has no header row, or the header doesn't contain it.
        """
        if not rows:
            return

        key_column = self.sheets_config.row_key_column
        if key_column is None:
            raise ValueError(
                "upsert_rows requires sheets_config.row_key_column to be set "
                "— use append_rows for worksheets with no row-key concept"
            )

        header = self._worksheet.row_values(1)
        if not header:
            raise ValueError(
                "worksheet has no header row — create it before writing rows"
            )
        if key_column not in header:
            raise ValueError(
                f"row_key_column {key_column!r} not found in worksheet header {header!r}"
            )

        existing_values = self._worksheet.get_all_values()
        key_index = header.index(key_column)
        # Row 1 is the header; data starts at sheet row 2.
        row_number_by_key: dict[str, int] = {
            data_row[key_index]: sheet_row_number
            for sheet_row_number, data_row in enumerate(existing_values[1:], start=2)
            if len(data_row) > key_index
        }

        updates: list[dict[str, Any]] = []
        appends: list[list[Any]] = []

        for row in rows:
            values = [row.get(column, "") for column in header]
            key_value = str(row.get(key_column, ""))
            sheet_row_number = row_number_by_key.get(key_value)
            if sheet_row_number is not None:
                end_cell = gspread.utils.rowcol_to_a1(sheet_row_number, len(header))
                updates.append(
                    {"range": f"A{sheet_row_number}:{end_cell}", "values": [values]}
                )
            else:
                appends.append(values)

        if updates:
            self._worksheet.batch_update(updates)
        if appends:
            self._append_rows(appends, existing_row_count=len(existing_values))

        logger.info(
            "wrote rows to sheet",
            updated=len(updates),
            appended=len(appends),
            worksheet_name=self.sheets_config.worksheet_name,
        )
