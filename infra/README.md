# Infrastructure

Docker, Compose, deployment scripts, and CI checks live here. Run commands from the repository root:

```sh
cp infra/.env.example .env
bash infra/scripts/compose.sh up -d --build
bash infra/scripts/compose.sh ps
bash infra/scripts/compose.sh logs --tail=100 drift-guard
```

The wrapper keeps `.env` at the repository root and preserves the `drift-guard-agent` Compose project name used on EC2. Set `DRIFT_GUARD_ENVIRONMENT=production` in `.env` on EC2.

Build and deploy a release, or restore its previous image:

```sh
docker build -f infra/Dockerfile -t drift-guard-agent:RELEASE .
bash infra/scripts/release.sh deploy drift-guard-agent:RELEASE
bash infra/scripts/release.sh rollback
```

CI runs `infra/ci/checks.sh` and `infra/scripts/check-rollback.sh`. GitHub's workflow entry point remains in `.github/workflows/ci.yml`. Nginx configuration remains on the EC2 host.
