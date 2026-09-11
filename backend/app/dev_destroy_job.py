"""Local-only cleanup helper: runs `terraform destroy` for a succeeded AWS EC2
provisioning job and marks it cancelled. Not exposed via the API — see
app/terraform_runner.py:run_destroy for why (§8.5 already-provisioned resource control
is meant to go through SDK-based /resources/action once resource-sync exists).

    docker compose run --rm api python -m app.dev_destroy_job <job_id>

Reuses the job's stored (encrypted) credential — no AWS keys need to be re-supplied.
"""

from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path

from app.config import get_settings
from app.db import SessionLocal
from app.models import Credential, ProvisioningJob
from app.security.credential_crypto import decrypt_credential_json
from app.terraform_runner import run_destroy


def main() -> None:
    if len(sys.argv) != 2:
        print("usage: python -m app.dev_destroy_job <job_id>", file=sys.stderr)
        raise SystemExit(1)

    job_id = int(sys.argv[1])
    db = SessionLocal()
    try:
        job = db.get(ProvisioningJob, job_id)
        if job is None:
            print(f"job {job_id} not found", file=sys.stderr)
            raise SystemExit(1)
        if job.status != "success":
            print(f"job {job_id} 상태가 '{job.status}'라 destroy 대상이 아닙니다.", file=sys.stderr)
            raise SystemExit(1)

        credential = db.get(Credential, job.credential_id)
        payload = decrypt_credential_json(credential.encrypted_payload, credential.encryption_nonce)
        env = {"AWS_ACCESS_KEY_ID": payload["access_key_id"], "AWS_SECRET_ACCESS_KEY": payload["secret_access_key"]}
        if payload.get("session_token"):
            env["AWS_SESSION_TOKEN"] = payload["session_token"]

        workspace_dir = Path(get_settings().terraform_workspaces_dir) / job.workspace_name
        result = run_destroy(workspace_dir, env)
        if not result.success:
            print(f"destroy 실패: {result.error_code} {result.error_message}", file=sys.stderr)
            raise SystemExit(1)

        job.status = "cancelled"
        job.finished_at = dt.datetime.now(dt.timezone.utc)
        db.commit()
        print(f"job {job_id} destroy 완료 (status={job.status})")
    finally:
        db.close()


if __name__ == "__main__":
    main()
