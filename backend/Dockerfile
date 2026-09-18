FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

ARG TERRAFORM_VERSION=1.9.8

# app/terraform_runner.py가 subprocess로 이 terraform CLI를 구동한다(§10 프로비저닝).
# HashiCorp apt repo 대신 공식 zip을 직접 받아 curl/unzip을 설치 직후 다시 지운다(빌드 후
# 이미지에 남기지 않기 위함 — terraform 바이너리 자체만 남으면 된다).
#
# 아키텍처 감지(2026-09-18 수정): 예전엔 linux_amd64로 고정돼 있었는데, Apple Silicon Mac에서
# Colima/Docker Desktop(ARM64 VM)으로 빌드하면 amd64 terraform CLI + provider 플러그인이 QEMU
# 에뮬레이션으로 돌아간다. 이게 불안정해서 단순한 리소스(Storage Account)는 우연히 통과해도
# VM/Database/CDN처럼 azurerm SDK를 더 많이 타는 코드 경로에서 플러그인이 SIGBUS로 크래시했다
# (실제 Azure 자격증명으로 재현·확인함 — TERRAFORM_ERROR: "Failed to read any lines from
# plugin's stdout" / "plugin crashed"). `dpkg --print-architecture`로 빌드 아키텍처를 감지해 맞는
# 바이너리를 받으면, terraform init이 provider도 자동으로 네이티브 arm64를 받아 에뮬레이션 자체가
# 없어진다(HashiCorp가 azurerm/aws/google 전부 linux_arm64 빌드를 배포한다).
RUN apt-get update \
    && apt-get install -y --no-install-recommends libpq5 curl unzip \
    && TERRAFORM_ARCH="$(dpkg --print-architecture)" \
    && curl -fsSL -o /tmp/terraform.zip \
        "https://releases.hashicorp.com/terraform/${TERRAFORM_VERSION}/terraform_${TERRAFORM_VERSION}_linux_${TERRAFORM_ARCH}.zip" \
    && unzip -o /tmp/terraform.zip -d /usr/local/bin \
    && rm /tmp/terraform.zip \
    && apt-get purge -y --auto-remove curl unzip \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN chmod +x docker-entrypoint.sh \
    && useradd --create-home --uid 1000 appuser \
    && mkdir -p /app/terraform_workspaces /app/terraform_plugin_cache \
    && chown -R appuser:appuser /app

USER appuser

ENTRYPOINT ["./docker-entrypoint.sh"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
