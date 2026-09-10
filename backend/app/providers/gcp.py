from __future__ import annotations

from google.api_core.exceptions import GoogleAPICallError
from google.auth.exceptions import GoogleAuthError
from google.cloud import resourcemanager_v3
from google.oauth2 import service_account

from app.providers import VerificationResult


def verify(external_account_id: str, secret_payload: dict) -> VerificationResult:
    try:
        credentials = service_account.Credentials.from_service_account_info(secret_payload)
        client = resourcemanager_v3.ProjectsClient(credentials=credentials)
        client.get_project(name=f"projects/{external_account_id}")
    except (GoogleAuthError, GoogleAPICallError, ValueError, KeyError):
        return VerificationResult(verified=False, error_code="PROVIDER_AUTHENTICATION_FAILED")

    scope = {"inventory_read": False, "resource_control": False, "provision": False, "cost_read": False}

    try:
        from google.cloud import compute_v1

        instances_client = compute_v1.InstancesClient(credentials=credentials)
        next(iter(instances_client.aggregated_list(project=external_account_id)), None)
        scope["inventory_read"] = True
    except (GoogleAPICallError, GoogleAuthError):
        pass

    return VerificationResult(verified=True, permission_scope=scope)
