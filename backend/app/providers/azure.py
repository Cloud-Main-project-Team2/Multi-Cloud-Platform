from __future__ import annotations

from azure.core.exceptions import AzureError, ClientAuthenticationError, HttpResponseError
from azure.identity import ClientSecretCredential
from azure.mgmt.resource import ResourceManagementClient
from azure.mgmt.resource.subscriptions import SubscriptionClient

from app.providers import VerificationResult


def verify(external_account_id: str, secret_payload: dict) -> VerificationResult:
    try:
        credential = ClientSecretCredential(
            tenant_id=secret_payload["tenant_id"],
            client_id=secret_payload["client_id"],
            client_secret=secret_payload["client_secret"],
        )
        subscription_client = SubscriptionClient(credential)
        subscription_client.subscriptions.get(external_account_id)
    except (ClientAuthenticationError, HttpResponseError, AzureError, KeyError):
        return VerificationResult(verified=False, error_code="PROVIDER_AUTHENTICATION_FAILED")

    scope = {"inventory_read": False, "resource_control": False, "provision": False, "cost_read": False}

    try:
        resource_client = ResourceManagementClient(credential, external_account_id)
        next(iter(resource_client.resources.list()), None)
        scope["inventory_read"] = True
    except (ClientAuthenticationError, HttpResponseError, AzureError):
        pass

    return VerificationResult(verified=True, permission_scope=scope)
