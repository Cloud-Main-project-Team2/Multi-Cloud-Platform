# 기술 스택

상태: Done
작업 날짜: 2026년 9월 3일

| 영역 | 스택 |
| --- | --- |
| 운영체제 | Ubuntu 24.04 LTS |
| 프론트엔드 | Javascript/HTML/tailwindCSS + React.js |
| 백엔드 | Python, FastAPI |
| DB | PostgreSQL |
| 인증 | JWT 인증 |
| 인프라(프로비저닝) | **Terraform (**리소스 생성만 담당) |
| 클라우드 SDK | boto3(AWS), azure-identity/azure-mgmt-(Azure), google-cloud-/googleapiclient(GCP) (조회·중지·삭제·비용 추정에 사용) |
| 컨테이너 | Docker, docker-compose (db / api / web 3개 서비스) |
| 로깅 | Python `logging` (RotatingFileHandler)
문서: `logs/access.log`(요청 단위) + `logs/app.log`(비즈니스 로직/클라우드 API 호출) |