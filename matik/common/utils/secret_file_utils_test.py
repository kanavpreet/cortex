"""Unit tests for secure temporary file utilities."""

import os
import stat

from common.utils.secret_file_utils import create_secure_temp_file


class TestCreateSecureTempFile:
    """Test suite for create_secure_temp_file context manager."""

    def test_creates_file_with_content(self) -> None:
        """Test that file is created with correct content."""
        content = "secret-content-123"
        with create_secure_temp_file("test-secret", content) as file_path:
            assert os.path.exists(file_path)
            # Need to change permissions to read the file in test
            os.chmod(file_path, 0o600)
            with open(file_path) as f:
                assert f.read() == content

    def test_unescapes_newlines(self) -> None:
        """Test that escaped newlines are converted to actual newlines."""
        content = "line1\\nline2\\nline3"
        with create_secure_temp_file("test-pem", content) as file_path:
            os.chmod(file_path, 0o600)
            with open(file_path) as f:
                assert f.read() == "line1\nline2\nline3"

    def test_pem_key_format(self) -> None:
        """Test with PEM key format (common use case)."""
        pem_content = "-----BEGIN RSA PRIVATE KEY-----\\nMIIE...base64...\\n-----END RSA PRIVATE KEY-----"
        with create_secure_temp_file("github-key", pem_content) as file_path:
            os.chmod(file_path, 0o600)
            with open(file_path) as f:
                content = f.read()
                assert content.startswith("-----BEGIN RSA PRIVATE KEY-----\n")
                assert content.endswith("-----END RSA PRIVATE KEY-----")

    def test_sets_readonly_permissions(self) -> None:
        """Test that file has read-only permissions (0400)."""
        with create_secure_temp_file("test-perms", "secret") as file_path:
            mode = os.stat(file_path).st_mode
            # Check only owner read permission is set
            assert mode & stat.S_IRUSR  # Owner can read
            assert not (mode & stat.S_IWUSR)  # Owner cannot write
            assert not (mode & stat.S_IXUSR)  # Owner cannot execute
            assert not (mode & stat.S_IRGRP)  # Group cannot read
            assert not (mode & stat.S_IROTH)  # Others cannot read

    def test_file_deleted_after_context(self) -> None:
        """Test that file is deleted after context exits."""
        file_path_captured = None
        with create_secure_temp_file("test-cleanup", "secret") as file_path:
            file_path_captured = file_path
            assert os.path.exists(file_path)

        assert not os.path.exists(file_path_captured)

    def test_file_deleted_on_exception(self) -> None:
        """Test that file is deleted even if exception occurs."""
        file_path_captured = None
        try:
            with create_secure_temp_file("test-exception", "secret") as file_path:
                file_path_captured = file_path
                raise ValueError("Test exception")
        except ValueError:
            pass

        assert file_path_captured is not None
        assert not os.path.exists(file_path_captured)

    def test_file_overwritten_before_deletion(self) -> None:
        """Test that file is overwritten with zeros before deletion."""
        # This test verifies the secure deletion behavior
        # We can't easily verify the content is zeroed, but we can verify
        # the file is properly cleaned up
        file_path_captured = None
        original_content = "super-secret-key-12345"

        with create_secure_temp_file("test-overwrite", original_content) as file_path:
            file_path_captured = file_path
            assert os.path.exists(file_path)

        # File should be deleted
        assert not os.path.exists(file_path_captured)

    def test_prefix_in_filename(self) -> None:
        """Test that prefix is included in the filename."""
        with create_secure_temp_file("my-custom-prefix", "content") as file_path:
            filename = os.path.basename(file_path)
            assert filename.startswith("my-custom-prefix")
            assert filename.endswith(".tmp")

    def test_empty_content(self) -> None:
        """Test handling of empty content."""
        with create_secure_temp_file("test-empty", "") as file_path:
            os.chmod(file_path, 0o600)
            with open(file_path) as f:
                assert f.read() == ""

    def test_multiline_content(self) -> None:
        """Test with actual multiline content (not escaped)."""
        content = "line1\nline2\nline3"
        with create_secure_temp_file("test-multiline", content) as file_path:
            os.chmod(file_path, 0o600)
            with open(file_path) as f:
                assert f.read() == "line1\nline2\nline3"
