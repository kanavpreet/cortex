"""Unit tests for hash utilities."""

from common.utils.hash_utils import generate_string_hash


class TestGenerateStringHash:
    """Test suite for generate_string_hash function."""

    def test_generates_sha256_hash(self) -> None:
        """Test that function generates correct SHA256 hash."""
        # Known SHA256 hash for "hello"
        result = generate_string_hash("hello")
        expected = "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824"
        assert result == expected

    def test_empty_string(self) -> None:
        """Test hash of empty string."""
        result = generate_string_hash("")
        # SHA256 of empty string
        expected = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
        assert result == expected

    def test_different_inputs_different_hashes(self) -> None:
        """Test that different inputs produce different hashes."""
        hash1 = generate_string_hash("hello")
        hash2 = generate_string_hash("world")
        assert hash1 != hash2

    def test_same_input_same_hash(self) -> None:
        """Test that same input always produces same hash."""
        hash1 = generate_string_hash("test string")
        hash2 = generate_string_hash("test string")
        assert hash1 == hash2

    def test_hash_length(self) -> None:
        """Test that hash is always 64 characters (256 bits in hex)."""
        assert len(generate_string_hash("short")) == 64
        assert len(generate_string_hash("a" * 1000)) == 64
