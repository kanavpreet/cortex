"""Unit tests for AWS IAM utility functions."""

from unittest.mock import MagicMock, patch

from common.utils.iam_utils import assume_role, get_session


class TestAssumeRole:
    """Test suite for assume_role function."""

    @patch("common.utils.iam_utils.boto3.client")
    @patch("common.utils.iam_utils.boto3.Session")
    def test_assume_role_success(
        self, mock_session_class: MagicMock, mock_client: MagicMock
    ) -> None:
        """Test successful role assumption."""
        # Setup mock STS client response
        mock_sts = MagicMock()
        mock_client.return_value = mock_sts
        mock_sts.assume_role.return_value = {
            "Credentials": {
                "AccessKeyId": "AKIAIOSFODNN7EXAMPLE",
                "SecretAccessKey": "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
                "SessionToken": "FwoGZXIvYXdzEBYaDK...",
            }
        }

        # Setup mock session
        mock_session = MagicMock()
        mock_session_class.return_value = mock_session

        # Call the function
        result = assume_role(
            role_arn="arn:aws:iam::123456789012:role/TestRole",
            region="us-east-1",
            session_name="test-session",
        )

        # Verify STS client was created with correct region
        mock_client.assert_called_once_with("sts", region_name="us-east-1")

        # Verify assume_role was called correctly
        mock_sts.assume_role.assert_called_once_with(
            RoleArn="arn:aws:iam::123456789012:role/TestRole",
            RoleSessionName="test-session",
        )

        # Verify session was created with assumed credentials
        mock_session_class.assert_called_once_with(
            aws_access_key_id="AKIAIOSFODNN7EXAMPLE",
            aws_secret_access_key="wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
            aws_session_token="FwoGZXIvYXdzEBYaDK...",
            region_name="us-east-1",
        )

        assert result == mock_session

    @patch("common.utils.iam_utils.boto3.client")
    @patch("common.utils.iam_utils.boto3.Session")
    def test_assume_role_default_session_name(
        self, mock_session_class: MagicMock, mock_client: MagicMock
    ) -> None:
        """Test that default session name is used."""
        mock_sts = MagicMock()
        mock_client.return_value = mock_sts
        mock_sts.assume_role.return_value = {
            "Credentials": {
                "AccessKeyId": "AKID",
                "SecretAccessKey": "SECRET",
                "SessionToken": "TOKEN",
            }
        }
        mock_session_class.return_value = MagicMock()

        assume_role(role_arn="arn:aws:iam::123456789012:role/TestRole")

        mock_sts.assume_role.assert_called_once_with(
            RoleArn="arn:aws:iam::123456789012:role/TestRole",
            RoleSessionName="matik-session",
        )

    @patch("common.utils.iam_utils.boto3.client")
    @patch("common.utils.iam_utils.boto3.Session")
    def test_assume_role_no_region(
        self, mock_session_class: MagicMock, mock_client: MagicMock
    ) -> None:
        """Test assume_role with no region specified."""
        mock_sts = MagicMock()
        mock_client.return_value = mock_sts
        mock_sts.assume_role.return_value = {
            "Credentials": {
                "AccessKeyId": "AKID",
                "SecretAccessKey": "SECRET",
                "SessionToken": "TOKEN",
            }
        }
        mock_session_class.return_value = MagicMock()

        assume_role(role_arn="arn:aws:iam::123456789012:role/TestRole")

        mock_client.assert_called_once_with("sts", region_name=None)
        mock_session_class.assert_called_once_with(
            aws_access_key_id="AKID",
            aws_secret_access_key="SECRET",
            aws_session_token="TOKEN",
            region_name=None,
        )

    @patch("common.utils.iam_utils.boto3.Session")
    def test_assume_role_with_existing_session(
        self, mock_session_class: MagicMock
    ) -> None:
        """Test assume_role uses provided session for chained assumption."""
        # Setup existing session
        existing_session = MagicMock()
        mock_sts = MagicMock()
        existing_session.client.return_value = mock_sts
        mock_sts.assume_role.return_value = {
            "Credentials": {
                "AccessKeyId": "CHAINED_AKID",
                "SecretAccessKey": "CHAINED_SECRET",
                "SessionToken": "CHAINED_TOKEN",
            }
        }

        # Setup return session
        new_session = MagicMock()
        mock_session_class.return_value = new_session

        # Call with existing session (chained assumption)
        result = assume_role(
            role_arn="arn:aws:iam::222222222222:role/SecondRole",
            region="us-east-1",
            session=existing_session,
        )

        # Verify STS client was created from existing session, not boto3.client
        existing_session.client.assert_called_once_with("sts", region_name="us-east-1")

        # Verify assume_role was called on the existing session's STS client
        mock_sts.assume_role.assert_called_once_with(
            RoleArn="arn:aws:iam::222222222222:role/SecondRole",
            RoleSessionName="matik-session",
        )

        # Verify new session was created with chained credentials
        mock_session_class.assert_called_once_with(
            aws_access_key_id="CHAINED_AKID",
            aws_secret_access_key="CHAINED_SECRET",
            aws_session_token="CHAINED_TOKEN",
            region_name="us-east-1",
        )

        assert result == new_session


class TestGetSession:
    """Test suite for get_session function."""

    @patch("common.utils.iam_utils.assume_role")
    def test_get_session_with_role_arn(self, mock_assume_role: MagicMock) -> None:
        """Test get_session assumes role when role_arn is provided."""
        mock_session = MagicMock()
        mock_assume_role.return_value = mock_session

        result = get_session(
            role_arn="arn:aws:iam::123456789012:role/TestRole",
            region="us-west-2",
            session_name="custom-session",
        )

        mock_assume_role.assert_called_once_with(
            "arn:aws:iam::123456789012:role/TestRole",
            "us-west-2",
            "custom-session",
        )
        assert result == mock_session

    @patch("common.utils.iam_utils.boto3.Session")
    def test_get_session_without_role_arn(self, mock_session_class: MagicMock) -> None:
        """Test get_session returns default session when no role_arn."""
        mock_session = MagicMock()
        mock_session_class.return_value = mock_session

        result = get_session(region="us-east-1")

        mock_session_class.assert_called_once_with(region_name="us-east-1")
        assert result == mock_session

    @patch("common.utils.iam_utils.boto3.Session")
    def test_get_session_no_args(self, mock_session_class: MagicMock) -> None:
        """Test get_session with no arguments."""
        mock_session = MagicMock()
        mock_session_class.return_value = mock_session

        result = get_session()

        mock_session_class.assert_called_once_with(region_name=None)
        assert result == mock_session

    @patch("common.utils.iam_utils.boto3.Session")
    def test_get_session_empty_role_arn(self, mock_session_class: MagicMock) -> None:
        """Test get_session with empty string role_arn returns default session."""
        mock_session = MagicMock()
        mock_session_class.return_value = mock_session

        # Empty string is falsy, so should return default session
        result = get_session(role_arn="", region="us-east-1")

        mock_session_class.assert_called_once_with(region_name="us-east-1")
        assert result == mock_session
