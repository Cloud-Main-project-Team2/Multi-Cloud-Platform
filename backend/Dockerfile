FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

ARG TERRAFORM_VERSION=1.9.8

# app/terraform_runner.py가 subprocess로 이 terraform CLI를 구동한다(§10 프로비저닝).
# HashiCorp apt repo 대신 공식 zip을 직접 받아 curl/unzip을 설치 직후 다시 지운다(빌드 후
# 이미지에 남기지 않기 위함 — terraform 바이너리 자체만 남으면 된다).
RUN apt-get update \
    && apt-get install -y --no-install-recommends libpq5 curl unzip \
    && curl -fsSL -o /tmp/terraform.zip \
        "https://releases.hashicorp.com/terraform/${TERRAFORM_VERSION}/terraform_${TERRAFORM_VERSION}_linux_amd64.zip" \
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
