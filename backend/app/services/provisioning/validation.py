"""common_spec/provider_spec에 secret 필드가 섞여 있는지 검사.

`docs/01_API_명세서_v1.1.md` 10.3절: "common_spec과 provider_spec에는 secret을
허용하지 않는다. secret 필드가 감지되면 422 SECRET_FIELD_NOT_ALLOWED로 거부한다."

"private_key"는 막지만 "ssh_public_key" 같은 공개키 필드는 의도적으로 통과시킨다.
"""

from __future__ import annotations

SECRET_FIELD_SUBSTRINGS = ("password", "secret", "private_key", "access_key", "token", "credential")


def find_secret_field(spec: dict) -> str | None:
    """secret으로 의심되는 첫 번째 키 이름을 반환한다. 없으면 None."""
    for key in spec:
        lowered = key.lower()
        if any(marker in lowered for marker in SECRET_FIELD_SUBSTRINGS):
            return key
    return None
