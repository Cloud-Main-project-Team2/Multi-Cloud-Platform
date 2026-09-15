# docs

확정 기획·설계 문서 보관용.

> **파일명 규칙**: 문서 파일명은 **영어(ASCII)** 로 쓴다 — 셸/`git mv`/URL에서 공백·한글·
> `·`·`—` 가 섞인 경로가 매번 인용부호를 요구하고, CI·스크립트에서 깨지기 쉽다. 문서 **내용**은
> 한국어 그대로 쓴다. (2026-09-15에 기존 한글 파일명을 일괄 변경했다.)

현재 포함된 문서:
- `01_API_Specification_v1.1.md` — API 명세
- `DB_ERD_v1.1.md` — DB ERD
- `Functional_Specification.html` — 기능 명세서
- `Screen_Design_v1.1.html` — 화면설계서 V1.1
- `Tech_Stack.md` — 기술 스택
- `Multicloud_Provider_Feature_Mapping_2026-09-10.md` — 3사 설정값 맵핑
- `Azure_Storage_Database_Provisioning_Decisions_2026-09-14.md` — 프로비저닝 결정사항
- `change_iam.md` — IAM 인증 전환 설계안의 코드 적용 가능성 검증(2026-09-14)
- `IAM_Delegation_and_Team_Budget_Design_2026-09-15.md` — IAM 위임 전환 + 팀별 예산 설계(검증 반영본)
- `AWS_AssumeRole_Delegation_Explainer.md` — 역할 위임 인증 발표용 개념 설명(IAM 개념·토큰 발급 과정·Q&A)
- `design/` — 디자인 스크린샷(색·타이포·컴포넌트 룩 참고용), `design/extensions/` — 확장 기능 시안

> WBS·프로토타입 개발 프롬프트·Tier2 확장 프롬프트 등 일부 문서는 아직 이 레포가 아니라
> Claude 프로젝트 컨텍스트에 있다(CLAUDE.md의 Pointer 섹션 참고). 필요 시 팀이 여기 추가한다.
