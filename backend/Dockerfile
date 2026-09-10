FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

ARG TERRAFORM_VERSION=1.7.5

# libpq5: psycopg2-binary 런타임 의존. curl/unzip: terraform CLI 설치용(설치 후 제거하지 않음 —
# terraform은 app/services/provisioning이 매 요청마다 subprocess로 호출한다).
RUN apt-get update \
    && apt-get install -y --no-install-recommends libpq5 curl unzip \
    && arch="$(dpkg --print-architecture)" \
    && curl -fsSL -o /tmp/terraform.zip \
         "https://releases.hashicorp.com/terraform/${TERRAFORM_VERSION}/terraform_${TERRAFORM_VERSION}_linux_${arch}.zip" \
    && unzip -o /tmp/terraform.zip -d /usr/local/bin \
    && rm /tmp/terraform.zip \
    && apt-get purge -y --auto-remove unzip \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN chmod +x docker-entrypoint.sh \
    && useradd --create-home --uid 1000 appuser \
    && chown -R appuser:appuser /app

USER appuser

ENTRYPOINT ["./docker-entrypoint.sh"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
