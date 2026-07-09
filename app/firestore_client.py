"""Firestore クライアントの遅延シングルトン。"""

from google.cloud import firestore

from app import config

CASES_COLLECTION = "support_cases"
INCIDENTS_COLLECTION = "incident_logs"

_client: firestore.Client | None = None


def get_client() -> firestore.Client:
    global _client
    if _client is None:
        _client = firestore.Client(
            project=config.get_project_id(),
            database=config.FIRESTORE_DATABASE,
        )
    return _client
