"""RUN-ADDRESS-LOCATION, DOC-OPEN-SAVE — a folder location read through the Workspace API.

A Databricks App cannot open ``/Workspace`` as a filesystem; it reaches it through the Workspace API, as the
signed-in user. This is the [[LibraryFiles]] for that case, so a folder resolves by the same rules a notebook
uses (`BUILD-LIBRARY-PROVIDER`): the provider decides what a library is, this only lists and reads.

The client's own identity decides what can be read. Hand it the signed-in user's client, never the App's.
"""
from __future__ import annotations

from typing import Dict, List, Optional

from .libraries import LibraryFiles, glob_match


class WorkspaceLibraryFiles(LibraryFiles):
    """Workspace files through ``client.workspace``. One recursive listing per root, held for this
    object's life: make one per request, so a file saved a moment ago is seen.

    ``client`` is a ``WorkspaceClient`` or a function returning one. A function is called on first use, so a
    caller can hand one to every read and pay for a client only when a folder is actually read."""

    def __init__(self, client) -> None:
        self._client = client
        self._listing: Dict[str, List[str]] = {}

    @property
    def _workspace(self):
        if callable(self._client):
            self._client = self._client()
        return self._client.workspace

    def is_dir(self, path: str) -> bool:
        from databricks.sdk.errors import NotFound
        from databricks.sdk.service.workspace import ObjectType
        try:
            kind = self._workspace.get_status(path).object_type
        except NotFound:
            return False
        return kind in (ObjectType.DIRECTORY, ObjectType.REPO)

    def glob(self, root: str, pattern: str) -> List[str]:
        root = root.rstrip("/")
        return sorted(p for p in self._files_under(root) if glob_match(p[len(root) + 1:], pattern))

    def read_text(self, path: str) -> Optional[str]:
        from databricks.sdk.errors import NotFound
        try:
            with self._workspace.download(path) as body:
                data = body.read()
        except NotFound:
            return None
        try:
            return data.decode("utf-8")
        except UnicodeDecodeError:
            return None

    def _files_under(self, root: str) -> List[str]:
        if root not in self._listing:
            from databricks.sdk.service.workspace import ObjectType
            self._listing[root] = [o.path for o in self._workspace.list(root, recursive=True)
                                   if o.object_type == ObjectType.FILE and o.path]
        return self._listing[root]
