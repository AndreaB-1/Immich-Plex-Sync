"""Immich API client."""

import logging
from typing import Any

import requests

logger = logging.getLogger(__name__)


class ImmichClient:
    def __init__(self, base_url: str, api_key: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.session = requests.Session()
        self.session.headers.update({"x-api-key": api_key, "Accept": "application/json"})

    def _get(self, path: str, **kwargs: Any) -> Any:
        url = f"{self.base_url}{path}"
        response = self.session.get(url, timeout=30, **kwargs)
        response.raise_for_status()
        return response.json()

    def get_albums(self) -> list[dict]:
        """Return all albums for the authenticated user."""
        data = self._get("/api/albums")
        logger.debug("Fetched %d albums from Immich", len(data))
        return data

    def get_album(self, album_id: str) -> dict:
        """Return album detail including assets list."""
        return self._get(f"/api/albums/{album_id}")
