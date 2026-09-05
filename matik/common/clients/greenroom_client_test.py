"""Unit tests for greenroom_client.py."""

import unittest
from unittest.mock import MagicMock, patch

import pytest

from common.clients.greenroom_client import (
    GreenroomClient,
    create_greenroom_client,
)
from common.models.common_config import CommonConfig
from common.models.greenroom_config import GreenroomConfig
from common.models.greenroom_entity import GreenroomEntity


class TestGreenroomClientInitLocal(unittest.TestCase):
    """Tests for GreenroomClient initialization in local mode."""

    @patch("common.clients.greenroom_client.httpx.Client")
    def test_init_local_mode_success(self, mock_client_class: MagicMock) -> None:
        """Test successful initialization in local mode with IAP token."""
        mock_client = MagicMock()
        mock_client_class.return_value.__enter__ = MagicMock(return_value=mock_client)
        mock_client_class.return_value.__exit__ = MagicMock(return_value=False)

        mock_response = MagicMock()
        mock_response.json.return_value = {
            "backstageIdentity": {"token": "backstage-token-123"}
        }
        mock_client.get.return_value = mock_response

        config = GreenroomConfig(
            host="https://greenroom.example.com",
            api_token="api-token",
            iap_token="iap-token-123",
        )
        common_config = CommonConfig(environment="local")

        client = GreenroomClient(greenroom_config=config, common_config=common_config)

        assert client._is_local is True
        assert client._backstage_token == "backstage-token-123"
        assert client._host == "https://developers.a.musta.ch"

    def test_init_local_mode_missing_iap_token(self) -> None:
        """Test initialization fails in local mode without IAP token."""
        config = GreenroomConfig(
            host="https://greenroom.example.com",
            api_token="api-token",
        )
        common_config = CommonConfig(environment="local")

        with pytest.raises(ValueError, match="iap_token must be set"):
            GreenroomClient(greenroom_config=config, common_config=common_config)


class TestGreenroomClientInitKubernetes(unittest.TestCase):
    """Tests for GreenroomClient initialization in Kubernetes mode."""

    def test_init_kubernetes_mode_success(self) -> None:
        """Test successful initialization in Kubernetes mode."""
        config = GreenroomConfig(
            host="https://greenroom-internal.example.com",
            api_token="static-api-token",
        )
        common_config = CommonConfig(environment="production")

        client = GreenroomClient(greenroom_config=config, common_config=common_config)

        assert client._is_local is False
        assert client._backstage_token == "static-api-token"
        assert client._host == "https://greenroom-internal.example.com"

    def test_init_kubernetes_mode_missing_api_token(self) -> None:
        """Test initialization fails in Kubernetes mode without API token."""
        config = GreenroomConfig(
            host="https://greenroom-internal.example.com",
        )
        common_config = CommonConfig(environment="production")

        with pytest.raises(ValueError, match="api_token must be set"):
            GreenroomClient(greenroom_config=config, common_config=common_config)


class TestGreenroomClientGetEntity(unittest.TestCase):
    """Tests for GreenroomClient entity retrieval methods."""

    def _create_kubernetes_client(self) -> GreenroomClient:
        """Create a client in Kubernetes mode (no IAP token fetch)."""
        config = GreenroomConfig(
            host="https://greenroom.example.com",
            api_token="api-token",
        )
        common_config = CommonConfig(environment="production")
        return GreenroomClient(greenroom_config=config, common_config=common_config)

    @patch("common.clients.greenroom_client.httpx.Client")
    def test_get_entity_by_name_success(self, mock_client_class: MagicMock) -> None:
        """Test successful entity retrieval by name."""
        mock_client = MagicMock()
        mock_client_class.return_value.__enter__ = MagicMock(return_value=mock_client)
        mock_client_class.return_value.__exit__ = MagicMock(return_value=False)

        mock_response = MagicMock()
        mock_response.json.return_value = {
            "apiVersion": "backstage.io/v1alpha1",
            "kind": "Component",
            "metadata": {
                "name": "my-service",
                "namespace": "default",
                "uid": "uid-123",
                "description": "A test service",
            },
            "spec": {"type": "service"},
        }
        mock_client.get.return_value = mock_response

        client = self._create_kubernetes_client()
        entity = client.get_entity_by_name("component", "default", "my-service")

        assert isinstance(entity, GreenroomEntity)
        assert entity.name == "my-service"
        assert entity.namespace == "default"
        assert entity.kind == "Component"
        assert entity.description == "A test service"

    @patch("common.clients.greenroom_client.httpx.Client")
    def test_get_entity_by_uid_success(self, mock_client_class: MagicMock) -> None:
        """Test successful entity retrieval by UID."""
        mock_client = MagicMock()
        mock_client_class.return_value.__enter__ = MagicMock(return_value=mock_client)
        mock_client_class.return_value.__exit__ = MagicMock(return_value=False)

        mock_response = MagicMock()
        mock_response.json.return_value = {
            "apiVersion": "backstage.io/v1alpha1",
            "kind": "Component",
            "metadata": {
                "name": "my-service",
                "namespace": "default",
                "uid": "uid-123",
            },
        }
        mock_client.get.return_value = mock_response

        client = self._create_kubernetes_client()
        entity = client.get_entity_by_uid("uid-123")

        assert isinstance(entity, GreenroomEntity)
        assert entity.name == "my-service"


class TestGreenroomClientQueryEntities(unittest.TestCase):
    """Tests for GreenroomClient query methods."""

    def _create_kubernetes_client(self) -> GreenroomClient:
        """Create a client in Kubernetes mode."""
        config = GreenroomConfig(
            host="https://greenroom.example.com",
            api_token="api-token",
        )
        common_config = CommonConfig(environment="production")
        return GreenroomClient(greenroom_config=config, common_config=common_config)

    @patch("common.clients.greenroom_client.httpx.Client")
    def test_query_entities_single_page(self, mock_client_class: MagicMock) -> None:
        """Test querying entities with single page of results."""
        mock_client = MagicMock()
        mock_client_class.return_value.__enter__ = MagicMock(return_value=mock_client)
        mock_client_class.return_value.__exit__ = MagicMock(return_value=False)

        mock_response = MagicMock()
        mock_response.json.return_value = {
            "items": [
                {
                    "apiVersion": "backstage.io/v1alpha1",
                    "kind": "Component",
                    "metadata": {"name": "service-1", "namespace": "default"},
                },
                {
                    "apiVersion": "backstage.io/v1alpha1",
                    "kind": "Component",
                    "metadata": {"name": "service-2", "namespace": "default"},
                },
            ],
            "pageInfo": {"hasNextCursor": False},
        }
        mock_client.get.return_value = mock_response

        client = self._create_kubernetes_client()
        entities = client.query_entities(["kind=component"])

        assert len(entities) == 2
        assert entities[0].name == "service-1"
        assert entities[1].name == "service-2"

    @patch("common.clients.greenroom_client.httpx.Client")
    def test_query_entities_with_pagination(self, mock_client_class: MagicMock) -> None:
        """Test querying entities with multiple pages."""
        mock_client = MagicMock()
        mock_client_class.return_value.__enter__ = MagicMock(return_value=mock_client)
        mock_client_class.return_value.__exit__ = MagicMock(return_value=False)

        # First page
        first_page = MagicMock()
        first_page.json.return_value = {
            "items": [
                {
                    "apiVersion": "backstage.io/v1alpha1",
                    "kind": "Component",
                    "metadata": {"name": "service-1", "namespace": "default"},
                },
            ],
            "pageInfo": {"hasNextCursor": True, "nextCursor": "cursor-123"},
        }

        # Second page (last)
        second_page = MagicMock()
        second_page.json.return_value = {
            "items": [
                {
                    "apiVersion": "backstage.io/v1alpha1",
                    "kind": "Component",
                    "metadata": {"name": "service-2", "namespace": "default"},
                },
            ],
            "pageInfo": {"hasNextCursor": False},
        }

        mock_client.get.side_effect = [first_page, second_page]

        client = self._create_kubernetes_client()
        entities = client.query_entities(["kind=component"])

        assert len(entities) == 2
        assert mock_client.get.call_count == 2

    @patch("common.clients.greenroom_client.httpx.Client")
    def test_query_entities_respects_max_results(
        self, mock_client_class: MagicMock
    ) -> None:
        """Test that max_results limit is respected."""
        mock_client = MagicMock()
        mock_client_class.return_value.__enter__ = MagicMock(return_value=mock_client)
        mock_client_class.return_value.__exit__ = MagicMock(return_value=False)

        mock_response = MagicMock()
        # Return more items than max_results
        mock_response.json.return_value = {
            "items": [
                {
                    "apiVersion": "backstage.io/v1alpha1",
                    "kind": "Component",
                    "metadata": {"name": f"service-{i}", "namespace": "default"},
                }
                for i in range(10)
            ],
            "pageInfo": {"hasNextCursor": True, "nextCursor": "cursor-123"},
        }
        mock_client.get.return_value = mock_response

        client = self._create_kubernetes_client()
        entities = client.query_entities(
            ["kind=component"], page_size=100, max_results=5
        )

        assert len(entities) == 5


class TestGreenroomClientConvertEntity(unittest.TestCase):
    """Tests for entity conversion."""

    def _create_kubernetes_client(self) -> GreenroomClient:
        """Create a client in Kubernetes mode."""
        config = GreenroomConfig(
            host="https://greenroom.example.com",
            api_token="api-token",
        )
        common_config = CommonConfig(environment="production")
        return GreenroomClient(greenroom_config=config, common_config=common_config)

    def test_convert_entity_with_relations(self) -> None:
        """Test converting entity with relations."""
        client = self._create_kubernetes_client()

        data = {
            "apiVersion": "backstage.io/v1alpha1",
            "kind": "Component",
            "metadata": {
                "name": "my-service",
                "namespace": "default",
                "uid": "uid-123",
                "etag": "etag-456",
                "title": "My Service",
                "description": "A test service",
                "labels": {"team": "platform"},
                "annotations": {"pagerduty/integration-key": "key-123"},
                "tags": ["python", "backend"],
            },
            "spec": {"type": "service", "owner": "team-a"},
            "relations": [
                {"type": "ownedBy", "targetRef": "group:default/team-a"},
                {"type": "dependsOn", "targetRef": "component:default/database"},
            ],
        }

        entity = client._convert_to_matik_entity(data)

        assert entity.api_version == "backstage.io/v1alpha1"
        assert entity.kind == "Component"
        assert entity.name == "my-service"
        assert entity.namespace == "default"
        assert entity.uid == "uid-123"
        assert entity.title == "My Service"
        assert entity.description == "A test service"
        assert entity.labels == {"team": "platform"}
        assert entity.tags == ["python", "backend"]
        assert entity.relations is not None
        assert len(entity.relations) == 2
        assert entity.relations[0].type == "ownedBy"
        assert entity.relations[0].target_ref == "group:default/team-a"

    def test_convert_empty_entity_raises(self) -> None:
        """Test that converting empty entity raises error."""
        client = self._create_kubernetes_client()

        with pytest.raises(ValueError, match="Cannot convert empty entity"):
            client._convert_to_matik_entity({})


class TestCreateGreenroomClient(unittest.TestCase):
    """Tests for create_greenroom_client factory function."""

    def test_create_greenroom_client_kubernetes(self) -> None:
        """Test factory function creates client correctly."""
        config = GreenroomConfig(
            host="https://greenroom.example.com",
            api_token="api-token",
        )
        common_config = CommonConfig(environment="production")

        client = create_greenroom_client(config, common_config)

        assert isinstance(client, GreenroomClient)
        assert client._is_local is False


class TestGreenroomClientBuildHeaders(unittest.TestCase):
    """Tests for header building."""

    def test_build_headers_kubernetes_mode(self) -> None:
        """Test headers in Kubernetes mode."""
        config = GreenroomConfig(
            host="https://greenroom.example.com",
            api_token="api-token",
        )
        common_config = CommonConfig(environment="production")

        client = GreenroomClient(greenroom_config=config, common_config=common_config)
        headers = client._build_headers()

        assert "Authorization" in headers
        assert headers["Authorization"] == "Bearer api-token"
        assert "Content-Type" in headers
        # No Proxy-Authorization in Kubernetes mode
        assert "Proxy-Authorization" not in headers


if __name__ == "__main__":
    unittest.main()
