"""terraform/CSP 실패 원문(raw stderr) → 친절한 한글 원인으로 승격하는 번역기.

`terraform_runner._classify_error`는 stderr를 `TERRAFORM_ERROR` 하나로 뭉뚱그리는 경우가 많다
(권한·인증 패턴만 별도 코드로 분리). 그래서 코드만으로는 "왜 실패했는지"를 알 수 없고, 저장된
원문은 대개 영어 CSP jargon이다. 이 모듈이 자주 나오는 원문 패턴을 매칭해 사용자가 바로 고칠 수
있는 한글 문장으로 바꾼다.

원문은 지우지 않는다 — 이 번역은 코드 카탈로그(`app.error_catalog`)의 고정 설명 위에 얹는
'구체 원인'이고, 매칭에 실패하면 원문을 그대로 '상세 원인'으로 노출한다(호출부 책임).

문구 초안 근거: `docs/Error_Catalog_Draft_2026-09-16.md` §2.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass(frozen=True)
class _Pattern:
    friendly: str
    keywords: tuple[str, ...] = ()  # 소문자 기준 부분 문자열 — 하나라도 있으면 매칭
    regex: str | None = None  # 소문자 기준 정규식 — search 성공하면 매칭
    _compiled: re.Pattern | None = field(default=None, compare=False, repr=False)

    def matches(self, lowered: str) -> bool:
        if any(k in lowered for k in self.keywords):
            return True
        if self.regex and re.search(self.regex, lowered):
            return True
        return False


# 순서 = 우선순위. 더 구체적인 패턴을 위에 둔다(첫 매칭이 이긴다).
_PATTERNS: tuple[_Pattern, ...] = (
    _Pattern(
        friendly="이름 규칙 위반입니다. 소문자·숫자·하이픈(-)만 쓸 수 있어요. 대문자·공백·언더스코어(_)는 사용할 수 없습니다.",
        keywords=(
            "invalidbucketname",
            "only lowercase",
            "must be lowercase",
            "lowercase alphanumeric",
        ),
        regex=r"bucket .*not valid|name .*must .*lowercase",
    ),
    _Pattern(
        friendly="이미 같은 이름이 존재합니다. 다른 이름으로 다시 시도하세요(이름이 전역에서 고유해야 합니다).",
        keywords=(
            "bucketalreadyexists",
            "bucketalreadyownedbyyou",
            "already exists",
            "alreadyexists",
            "entity already exists",
        ),
        regex=r"name .*is already|already in use",
    ),
    _Pattern(
        friendly="공인 IP(EIP) 할당량을 초과했습니다. 사용하지 않는 IP를 회수한 뒤 다시 시도하세요.",
        keywords=("addresslimitexceeded",),
    ),
    _Pattern(
        # 2026-09-18 실측: 실제 자격증명으로 Azure VM(B1s/B2s/D2s_v3, koreacentral/eastus)을
        # 만들다가 겪은 진짜 원인 — Azure 무료 체험 계정이 "무료 혜택 대상"이라고 안내하는
        # SKU라도, 이 구독·리전에는 애초에 그 SKU 용량이 배정돼 있지 않을 수 있다. 이건
        # 할당량(quota) 문제가 아니다 — quota는 "숫자를 늘려주면 되는" 문제지만, SkuNotAvailable은
        # "그 SKU 자체가 이 구독·리전엔 없다"는 뜻이라 할당량 증설로 해결되지 않는 경우가 흔하다
        # (아래 quota 패턴과 절대 같은 문구를 쓰지 않는다 — 원인이 다르므로 안내도 달라야 함).
        friendly="선택한 구독·리전에서 해당 VM SKU를 현재 사용할 수 없습니다. 다른 SKU·리전·가용 "
        "영역을 선택하거나 Azure 지원에 SKU 사용 요청을 하세요.",
        keywords=(
            "skunotavailable",
            "sku is not available",
        ),
        regex=r"capacit(y|ies) restriction|sku.*not available",
    ),
    _Pattern(
        # Azure의 vCPU 할당량 초과(리전 전체 또는 VM 계열별)는 SkuNotAvailable과 다른 문제다 —
        # 위 패턴과 혼동하지 않도록 Azure 특유의 키워드(OperationNotAllowed/ResourceQuotaExceeded,
        # "cores quota")로만 매칭한다. 무료 체험(free trial) 구독은 보통 할당량 증설 신청 대상이
        # 아니라는 점도 같이 안내한다(전부 할당량 문제라고 단정하지 않음 — SkuNotAvailable의
        # 다른 원인까지 이걸로 해결된다고 암시하지 않는다).
        friendly="전체 리전 vCPU 또는 VM 계열별 vCPU 할당량이 부족합니다. 리소스를 정리하거나 "
        "Azure에 할당량 증설을 요청하세요 — 다만 무료 체험(free trial) 구독은 보통 할당량 증설 "
        "대상이 아니므로, 계속 사용하려면 종량제(pay-as-you-go) 구독 전환이 필요할 수 있습니다.",
        keywords=(
            "operationnotallowed",
            "resourcequotaexceeded",
            "family cores quota",
            "regional cores quota",
        ),
        regex=r"exceed(ing|ed)? .*(cores|vcpu) quota|quota.*(cores|vcpu)",
    ),
    _Pattern(
        friendly="계정 할당량(quota)을 초과했습니다. 리소스를 정리하거나 CSP에 한도 증설을 요청하세요.",
        keywords=(
            "quotaexceeded",
            "limitexceeded",
            "vcpulimitexceeded",
            "too many",
        ),
        regex=r"exceed.*(quota|limit)|(quota|limit).*exceed",
    ),
    _Pattern(
        friendly="해당 리전/타입의 클라우드 용량이 부족합니다. 다른 인스턴스 타입이나 리전으로 시도하세요.",
        keywords=("insufficientinstancecapacity",),
        regex=r"insufficient .*capacity",
    ),
    _Pattern(
        friendly="선택한 리전에서 사용할 수 없거나 활성화되지 않은 리전입니다. 다른 리전을 선택하세요.",
        keywords=("not opted in", "optinrequired"),
        regex=r"unsupported .*region|region .*not (supported|enabled|available)",
    ),
    _Pattern(
        friendly="비밀번호 규칙 위반입니다. 마스터/관리자 비밀번호의 길이와 문자 종류 조건을 확인하세요.",
        keywords=("master password",),
        regex=r"password .*(policy|conform|requirement|must)",
    ),
    _Pattern(
        friendly="네트워크(VPC/서브넷) 설정에 문제가 있습니다. 네트워크 선택을 확인하세요.",
        keywords=(
            "no default vpc",
            "invalidvpcid",
            "invalidsubnetid",
            "invalidsubnet",
        ),
        regex=r"subnet .*not found|vpc .*not found",
    ),
    _Pattern(
        friendly="지정한 이미지(AMI/이미지)를 찾을 수 없습니다. 이미지 선택을 확인하세요.",
        keywords=("invalidamiid",),
        regex=r"ami .*does not exist|image .*not found",
    ),
    _Pattern(
        # 2026-09-17: "자격 증명에 권한을 추가하거나"는 무엇을 어디에 추가해야 하는지가 막연했다
        # — 실제로는 사용자가 자기 AWS 콘솔(IAM 역할)/Azure(RBAC)/GCP(서비스 계정 역할)에 가서
        # 권한을 추가해야 하는데, 그 사실과 "어디서 확인하면 되는지"(마이페이지)를 명시한다.
        friendly="권한이 부족합니다. 사용 중인 자격 증명(AWS는 IAM 역할, Azure/GCP는 각각의 역할·"
        "권한)에 이 작업에 필요한 권한이 없습니다 — 마이페이지의 해당 자격 증명 안내를 참고해 "
        "역할/정책에 권한을 추가한 뒤 다시 시도하세요.",
        keywords=(
            "accessdenied",
            "unauthorizedoperation",
            "is not authorized to perform",
            "does not have permission",
            "caller does not have permission",
        ),
    ),
    _Pattern(
        friendly="자격 증명이 만료되었거나 유효하지 않습니다. 마이페이지에서 재검증하세요.",
        keywords=(
            "invalidclienttokenid",
            "authfailure",
            "signaturedoesnotmatch",
            "invalid_grant",
            "invalid_client",
        ),
    ),
    _Pattern(
        friendly="시간 초과로 실패했습니다. 잠시 후 다시 시도하세요.",
        keywords=("timeout", "context deadline exceeded", "deadline exceeded"),
    ),
)


def translate_reason(raw_message: str | None, *, code: str | None = None) -> str | None:
    """원문 메시지를 친절한 한글 원인으로 번역한다. 매칭이 없으면 None.

    `code`는 향후 코드별로 번역을 게이팅하고 싶을 때를 위해 받아 두되, 현재는 사용하지 않는다
    (원문만 있으면 어느 코드든 동일하게 시도한다).
    """
    if not raw_message:
        return None
    lowered = raw_message.lower()
    for pattern in _PATTERNS:
        if pattern.matches(lowered):
            return pattern.friendly
    return None
