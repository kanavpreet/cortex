"""Unit tests for sheets_client.py.

Mocks gspread's network-facing entry points (``service_account``,
``service_account_from_dict``) — these tests never make a real Google API
call. Deliberately does NOT patch the whole ``gspread`` module: that would
also mock ``gspread.utils.rowcol_to_a1``, a pure helper the client relies on
for real.
"""

import json
import unittest
from unittest.mock import MagicMock, patch

import pytest

from common.clients.sheets_client import SheetsClient, _entered_value, _find_table
from common.models.sheets_config import SheetsConfig


def _config(**overrides: object) -> SheetsConfig:
    defaults: dict[str, object] = {
        "spreadsheet_id": "sheet-123",
        "worksheet_name": "Audit",
        "row_key_column": "reference_id",
        "service_account_file": "/tmp/fake-creds.json",
    }
    defaults.update(overrides)
    return SheetsConfig(**defaults)


def _make_client(
    mock_service_account: MagicMock,
    table_id: str | None = None,
    table_end_row_index: int = 1,
) -> tuple[SheetsClient, MagicMock]:
    """Build a SheetsClient with a fully mocked gspread call chain.

    Defaults to no Table on the worksheet (matching plain, non-Table sheets)
    -- pass ``table_id`` to exercise the Table-aware append path instead.
    """
    mock_worksheet = MagicMock()
    mock_worksheet.id = 111
    mock_spreadsheet = MagicMock()
    mock_spreadsheet.worksheet.return_value = mock_worksheet
    mock_worksheet.spreadsheet = mock_spreadsheet
    tables = (
        [
            {
                "tableId": table_id,
                "range": {
                    "startRowIndex": 0,
                    "endRowIndex": table_end_row_index,
                    "startColumnIndex": 0,
                    "endColumnIndex": 2,
                },
            }
        ]
        if table_id is not None
        else []
    )
    mock_spreadsheet.fetch_sheet_metadata.return_value = {
        "sheets": [{"properties": {"sheetId": 111}, "tables": tables}]
    }
    mock_gspread_client = MagicMock()
    mock_gspread_client.open_by_key.return_value = mock_spreadsheet
    mock_service_account.return_value = mock_gspread_client

    client = SheetsClient(sheets_config=_config())
    return client, mock_worksheet


class TestSheetsClientInit(unittest.TestCase):
    """Tests for SheetsClient initialization / credential selection."""

    @patch("common.clients.sheets_client.gspread.service_account")
    def test_uses_service_account_file_when_no_json_given(
        self, mock_service_account: MagicMock
    ) -> None:
        _make_client(mock_service_account)

        mock_service_account.assert_called_once_with(
            filename="/tmp/fake-creds.json",
            scopes=["https://www.googleapis.com/auth/spreadsheets"],
        )

    @patch("common.clients.sheets_client.gspread.service_account_from_dict")
    @patch("common.clients.sheets_client.gspread.service_account")
    def test_uses_service_account_json_when_given(
        self, mock_service_account: MagicMock, mock_service_account_from_dict: MagicMock
    ) -> None:
        mock_worksheet = MagicMock()
        mock_spreadsheet = MagicMock()
        mock_spreadsheet.worksheet.return_value = mock_worksheet
        mock_spreadsheet.fetch_sheet_metadata.return_value = {"sheets": []}
        mock_gspread_client = MagicMock()
        mock_gspread_client.open_by_key.return_value = mock_spreadsheet
        mock_service_account_from_dict.return_value = mock_gspread_client

        creds = {"type": "service_account", "project_id": "test"}
        SheetsClient(
            sheets_config=_config(
                service_account_file=None, service_account_json=json.dumps(creds)
            )
        )

        mock_service_account_from_dict.assert_called_once_with(
            creds, scopes=["https://www.googleapis.com/auth/spreadsheets"]
        )
        mock_service_account.assert_not_called()

    def test_raises_when_no_credentials_configured(self) -> None:
        with pytest.raises(ValueError, match="needs either"):
            SheetsClient(
                sheets_config=_config(
                    service_account_file=None, service_account_json=None
                )
            )

    @patch("common.clients.sheets_client.gspread.service_account")
    def test_opens_spreadsheet_and_worksheet(
        self, mock_service_account: MagicMock
    ) -> None:
        mock_worksheet = MagicMock()
        mock_spreadsheet = MagicMock()
        mock_spreadsheet.worksheet.return_value = mock_worksheet
        mock_spreadsheet.fetch_sheet_metadata.return_value = {"sheets": []}
        mock_gspread_client = MagicMock()
        mock_gspread_client.open_by_key.return_value = mock_spreadsheet
        mock_service_account.return_value = mock_gspread_client

        SheetsClient(sheets_config=_config())

        mock_gspread_client.open_by_key.assert_called_once_with("sheet-123")
        mock_spreadsheet.worksheet.assert_called_once_with("Audit")


class TestFindTable(unittest.TestCase):
    """Tests for _find_table."""

    def test_returns_table_when_sheet_has_one(self) -> None:
        spreadsheet = MagicMock()
        table = {"tableId": "42", "range": {"endRowIndex": 1}}
        spreadsheet.fetch_sheet_metadata.return_value = {
            "sheets": [{"properties": {"sheetId": 111}, "tables": [table]}]
        }

        assert _find_table(spreadsheet, 111) == table

    def test_returns_none_when_sheet_has_no_table(self) -> None:
        spreadsheet = MagicMock()
        spreadsheet.fetch_sheet_metadata.return_value = {
            "sheets": [{"properties": {"sheetId": 111}, "tables": []}]
        }

        assert _find_table(spreadsheet, 111) is None

    def test_returns_none_when_sheet_id_does_not_match(self) -> None:
        spreadsheet = MagicMock()
        spreadsheet.fetch_sheet_metadata.return_value = {
            "sheets": [{"properties": {"sheetId": 999}, "tables": [{"tableId": "42"}]}]
        }

        assert _find_table(spreadsheet, 111) is None


class TestEnteredValue(unittest.TestCase):
    """Tests for _entered_value."""

    def test_bool_becomes_bool_value(self) -> None:
        assert _entered_value(True) == {"userEnteredValue": {"boolValue": True}}
        assert _entered_value(False) == {"userEnteredValue": {"boolValue": False}}

    def test_string_becomes_string_value(self) -> None:
        assert _entered_value("INC-1") == {"userEnteredValue": {"stringValue": "INC-1"}}

    def test_blank_becomes_empty_string_value(self) -> None:
        assert _entered_value("") == {"userEnteredValue": {"stringValue": ""}}


class TestSheetsClientReadRows(unittest.TestCase):
    """Tests for read_rows."""

    @patch("common.clients.sheets_client.gspread.service_account")
    def test_delegates_to_get_all_records(
        self, mock_service_account: MagicMock
    ) -> None:
        client, worksheet = _make_client(mock_service_account)
        worksheet.get_all_records.return_value = [
            {"reference_id": "INC-1", "audit_status": "scored"}
        ]

        rows = client.read_rows()

        assert rows == [{"reference_id": "INC-1", "audit_status": "scored"}]


class TestSheetsClientAppendRows(unittest.TestCase):
    """Tests for append_rows."""

    @patch("common.clients.sheets_client.gspread.service_account")
    def test_noop_on_empty_input(self, mock_service_account: MagicMock) -> None:
        client, worksheet = _make_client(mock_service_account)

        client.append_rows([])

        worksheet.row_values.assert_not_called()

    @patch("common.clients.sheets_client.gspread.service_account")
    def test_raises_when_no_header_row(self, mock_service_account: MagicMock) -> None:
        client, worksheet = _make_client(mock_service_account)
        worksheet.row_values.return_value = []

        with pytest.raises(ValueError, match="no header row"):
            client.append_rows([{"reference_id": "INC-1"}])

    @patch("common.clients.sheets_client.gspread.service_account")
    def test_appends_every_row_unconditionally(
        self, mock_service_account: MagicMock
    ) -> None:
        client, worksheet = _make_client(mock_service_account)
        worksheet.row_values.return_value = ["reference_id", "entity_id"]

        client.append_rows(
            [
                {"reference_id": "INC-1", "entity_id": "1"},
                {"reference_id": "INC-1", "entity_id": "2"},
            ]
        )

        worksheet.append_rows.assert_called_once_with([["INC-1", "1"], ["INC-1", "2"]])
        worksheet.get_all_values.assert_not_called()

    @patch("common.clients.sheets_client.gspread.service_account")
    def test_missing_dict_keys_become_blank_cells(
        self, mock_service_account: MagicMock
    ) -> None:
        client, worksheet = _make_client(mock_service_account)
        worksheet.row_values.return_value = ["reference_id", "entity_id"]

        client.append_rows([{"reference_id": "INC-1"}])

        worksheet.append_rows.assert_called_once_with([["INC-1", ""]])

    @patch("common.clients.sheets_client.gspread.service_account")
    def test_appends_via_table_aware_write_when_table_present(
        self, mock_service_account: MagicMock
    ) -> None:
        client, worksheet = _make_client(mock_service_account, table_id="42")
        worksheet.row_values.return_value = ["reference_id", "change_related"]
        worksheet.get_all_values.return_value = [["reference_id", "change_related"]]

        client.append_rows([{"reference_id": "INC-1", "change_related": True}])

        worksheet.append_rows.assert_not_called()
        worksheet.spreadsheet.batch_update.assert_called_once_with(
            {
                "requests": [
                    {
                        "updateTable": {
                            "table": {
                                "tableId": "42",
                                "range": {
                                    "startRowIndex": 0,
                                    "endRowIndex": 2,
                                    "startColumnIndex": 0,
                                    "endColumnIndex": 2,
                                    "sheetId": 111,
                                },
                            },
                            "fields": "range",
                        }
                    },
                    {
                        "updateCells": {
                            "rows": [
                                {
                                    "values": [
                                        {"userEnteredValue": {"stringValue": "INC-1"}},
                                        {"userEnteredValue": {"boolValue": True}},
                                    ]
                                }
                            ],
                            "fields": "userEnteredValue",
                            "start": {
                                "sheetId": 111,
                                "rowIndex": 1,
                                "columnIndex": 0,
                            },
                        }
                    },
                ]
            }
        )


class TestSheetsClientUpsertRows(unittest.TestCase):
    """Tests for upsert_rows."""

    @patch("common.clients.sheets_client.gspread.service_account")
    def test_noop_on_empty_input(self, mock_service_account: MagicMock) -> None:
        client, worksheet = _make_client(mock_service_account)

        client.upsert_rows([])

        worksheet.row_values.assert_not_called()

    @patch("common.clients.sheets_client.gspread.service_account")
    def test_raises_when_row_key_column_not_configured(
        self, mock_service_account: MagicMock
    ) -> None:
        mock_worksheet = MagicMock()
        mock_spreadsheet = MagicMock()
        mock_spreadsheet.worksheet.return_value = mock_worksheet
        mock_spreadsheet.fetch_sheet_metadata.return_value = {"sheets": []}
        mock_gspread_client = MagicMock()
        mock_gspread_client.open_by_key.return_value = mock_spreadsheet
        mock_service_account.return_value = mock_gspread_client

        client = SheetsClient(sheets_config=_config(row_key_column=None))

        with pytest.raises(ValueError, match="row_key_column to be set"):
            client.upsert_rows([{"reference_id": "INC-1"}])

    @patch("common.clients.sheets_client.gspread.service_account")
    def test_raises_when_no_header_row(self, mock_service_account: MagicMock) -> None:
        client, worksheet = _make_client(mock_service_account)
        worksheet.row_values.return_value = []

        with pytest.raises(ValueError, match="no header row"):
            client.upsert_rows([{"reference_id": "INC-1"}])

    @patch("common.clients.sheets_client.gspread.service_account")
    def test_raises_when_row_key_column_missing_from_header(
        self, mock_service_account: MagicMock
    ) -> None:
        client, worksheet = _make_client(mock_service_account)
        worksheet.row_values.return_value = ["some_other_column"]

        with pytest.raises(ValueError, match="not found in worksheet header"):
            client.upsert_rows([{"reference_id": "INC-1"}])

    @patch("common.clients.sheets_client.gspread.service_account")
    def test_appends_new_row_when_key_not_present(
        self, mock_service_account: MagicMock
    ) -> None:
        client, worksheet = _make_client(mock_service_account)
        worksheet.row_values.return_value = ["reference_id", "audit_status"]
        worksheet.get_all_values.return_value = [["reference_id", "audit_status"]]

        client.upsert_rows([{"reference_id": "INC-1", "audit_status": "scored"}])

        worksheet.append_rows.assert_called_once_with([["INC-1", "scored"]])
        worksheet.batch_update.assert_not_called()

    @patch("common.clients.sheets_client.gspread.service_account")
    def test_appends_new_row_via_table_aware_write_when_table_present(
        self, mock_service_account: MagicMock
    ) -> None:
        client, worksheet = _make_client(mock_service_account, table_id="42")
        worksheet.row_values.return_value = ["reference_id", "audit_status"]
        worksheet.get_all_values.return_value = [["reference_id", "audit_status"]]

        client.upsert_rows([{"reference_id": "INC-1", "audit_status": "scored"}])

        worksheet.append_rows.assert_not_called()
        worksheet.spreadsheet.batch_update.assert_called_once_with(
            {
                "requests": [
                    {
                        "updateTable": {
                            "table": {
                                "tableId": "42",
                                "range": {
                                    "startRowIndex": 0,
                                    "endRowIndex": 2,
                                    "startColumnIndex": 0,
                                    "endColumnIndex": 2,
                                    "sheetId": 111,
                                },
                            },
                            "fields": "range",
                        }
                    },
                    {
                        "updateCells": {
                            "rows": [
                                {
                                    "values": [
                                        {"userEnteredValue": {"stringValue": "INC-1"}},
                                        {"userEnteredValue": {"stringValue": "scored"}},
                                    ]
                                }
                            ],
                            "fields": "userEnteredValue",
                            "start": {"sheetId": 111, "rowIndex": 1, "columnIndex": 0},
                        }
                    },
                ]
            }
        )

    @patch("common.clients.sheets_client.gspread.service_account")
    def test_appends_within_existing_table_range_without_growing_it(
        self, mock_service_account: MagicMock
    ) -> None:
        """If the table's range already covers the row being written to (e.g.
        pre-reserved capacity), no updateTable request is needed at all."""
        client, worksheet = _make_client(
            mock_service_account, table_id="42", table_end_row_index=10
        )
        worksheet.row_values.return_value = ["reference_id", "audit_status"]
        worksheet.get_all_values.return_value = [["reference_id", "audit_status"]]

        client.upsert_rows([{"reference_id": "INC-1", "audit_status": "scored"}])

        worksheet.spreadsheet.batch_update.assert_called_once_with(
            {
                "requests": [
                    {
                        "updateCells": {
                            "rows": [
                                {
                                    "values": [
                                        {"userEnteredValue": {"stringValue": "INC-1"}},
                                        {"userEnteredValue": {"stringValue": "scored"}},
                                    ]
                                }
                            ],
                            "fields": "userEnteredValue",
                            "start": {"sheetId": 111, "rowIndex": 1, "columnIndex": 0},
                        }
                    }
                ]
            }
        )

    @patch("common.clients.sheets_client.gspread.service_account")
    def test_uses_caller_provided_row_count_instead_of_refetching(
        self, mock_service_account: MagicMock
    ) -> None:
        """upsert_rows already knows the row count from its own
        get_all_values() call -- _append_rows shouldn't fetch it again."""
        client, worksheet = _make_client(mock_service_account, table_id="42")
        worksheet.row_values.return_value = ["reference_id", "audit_status"]
        worksheet.get_all_values.return_value = [
            ["reference_id", "audit_status"],
            ["INC-1", "parked_for_review"],
        ]

        client.upsert_rows([{"reference_id": "INC-2", "audit_status": "scored"}])

        assert worksheet.get_all_values.call_count == 1

    @patch("common.clients.sheets_client.gspread.service_account")
    def test_updates_existing_row_when_key_present(
        self, mock_service_account: MagicMock
    ) -> None:
        client, worksheet = _make_client(mock_service_account)
        worksheet.row_values.return_value = ["reference_id", "audit_status"]
        worksheet.get_all_values.return_value = [
            ["reference_id", "audit_status"],
            ["INC-1", "parked_for_review"],
        ]

        client.upsert_rows([{"reference_id": "INC-1", "audit_status": "scored"}])

        worksheet.batch_update.assert_called_once_with(
            [{"range": "A2:B2", "values": [["INC-1", "scored"]]}]
        )
        worksheet.append_rows.assert_not_called()

    @patch("common.clients.sheets_client.gspread.service_account")
    def test_missing_dict_keys_become_blank_cells(
        self, mock_service_account: MagicMock
    ) -> None:
        client, worksheet = _make_client(mock_service_account)
        worksheet.row_values.return_value = ["reference_id", "audit_status", "notes"]
        worksheet.get_all_values.return_value = [
            ["reference_id", "audit_status", "notes"]
        ]

        client.upsert_rows([{"reference_id": "INC-1", "audit_status": "scored"}])

        worksheet.append_rows.assert_called_once_with([["INC-1", "scored", ""]])

    @patch("common.clients.sheets_client.gspread.service_account")
    def test_batches_multiple_updates_and_appends_in_one_call_each(
        self, mock_service_account: MagicMock
    ) -> None:
        client, worksheet = _make_client(mock_service_account)
        worksheet.row_values.return_value = ["reference_id", "audit_status"]
        worksheet.get_all_values.return_value = [
            ["reference_id", "audit_status"],
            ["INC-1", "parked_for_review"],
            ["INC-2", "parked_for_review"],
        ]

        client.upsert_rows(
            [
                {"reference_id": "INC-1", "audit_status": "scored"},
                {"reference_id": "INC-2", "audit_status": "scored"},
                {"reference_id": "INC-3", "audit_status": "scored"},
            ]
        )

        assert worksheet.batch_update.call_count == 1
        assert len(worksheet.batch_update.call_args[0][0]) == 2
        assert worksheet.append_rows.call_count == 1
        assert worksheet.append_rows.call_args[0][0] == [["INC-3", "scored"]]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
