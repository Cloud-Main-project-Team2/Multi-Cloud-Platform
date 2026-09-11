from __future__ import annotations

from types import ModuleType

from app import aws_provisioning

_RUNNERS: dict[tuple[str, str], ModuleType] = {
    ("aws", "ec2"): aws_provisioning,
from app import gcp_provisioning

_RUNNERS: dict[tuple[str, str], ModuleType] = {
    ("gcp", "compute_engine"): gcp_provisioning,
}


def get_runner(provider: str, service_code: str) -> ModuleType | None:
    return _RUNNERS.get((provider, service_code))
