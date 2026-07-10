"""Generic Globus file inventory helpers."""

from __future__ import annotations

from typing import Any, Iterable


DEFAULT_GLOBUS_INVENTORY_SUFFIXES = frozenset(
    {
        ".h5",
        ".hdf5",
        ".cbf",
        ".html",
        ".xml",
        ".log",
        ".state",
        ".inp",
    }
)


class GlobusFileLister:
    """Recursively list selected files from a Globus endpoint path."""

    def __init__(self, transfer_client: Any, endpoint_id: str):
        if not str(endpoint_id).strip():
            raise ValueError("endpoint_id is required")
        self.transfer_client = transfer_client
        self.endpoint_id = str(endpoint_id).strip()

    def list_tree(
        self,
        path: str,
        include_suffixes: Iterable[str] | None = None,
    ) -> list[str]:
        """Return matching file paths below `path`, recursively.

        The transfer client must provide the Globus SDK-compatible method
        `operation_ls(endpoint_id, path=path)`. No transfer is submitted.
        """
        suffixes = self._normalize_suffixes(include_suffixes)
        results = self._list_tree(self._normalize_path(path), suffixes)
        return sorted(results)

    def _list_tree(self, path: str, include_suffixes: set[str]) -> list[str]:
        try:
            listing = self.transfer_client.operation_ls(self.endpoint_id, path=path)
        except Exception as exc:
            raise RuntimeError(
                f"Globus listing failed for {self.endpoint_id}:{path}"
            ) from exc

        matches: list[str] = []
        for item in listing:
            name = item.get("name")
            item_type = item.get("type")
            if not name:
                continue
            if "/" in str(name):
                raise ValueError(f"Globus item name must not contain '/': {name}")
            child_path = f"{path.rstrip('/')}/{str(name).lstrip('/')}"
            if item_type == "dir":
                matches.extend(self._list_tree(child_path, include_suffixes))
            elif item_type == "file" and self._matches_suffix(name, include_suffixes):
                matches.append(child_path)
        return matches

    @staticmethod
    def _normalize_path(path: str) -> str:
        text = str(path).strip()
        if not text:
            raise ValueError("path is required")
        return text.rstrip("/") or "/"

    @staticmethod
    def _normalize_suffixes(include_suffixes: Iterable[str] | None) -> set[str]:
        raw_suffixes = include_suffixes or DEFAULT_GLOBUS_INVENTORY_SUFFIXES
        suffixes = set()
        for suffix in raw_suffixes:
            text = str(suffix).strip().lower()
            if not text:
                continue
            suffixes.add(text if text.startswith(".") else f".{text}")
        return suffixes

    @staticmethod
    def _matches_suffix(name: str, include_suffixes: set[str]) -> bool:
        lower_name = str(name).lower()
        return any(lower_name.endswith(suffix) for suffix in include_suffixes)
