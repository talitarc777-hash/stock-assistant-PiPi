# GitHub Actions Auto-Deploy (NanoPi)

This project includes `.github/workflows/deploy-nanopi.yml` to auto-deploy backend changes to NanoPi when `main` is updated.

## 1. Add Repository Secrets

In GitHub repo -> `Settings` -> `Secrets and variables` -> `Actions`, add:

- `NANOPI_HOST`: NanoPi public host/IP
- `NANOPI_PORT`: SSH port (usually `22`)
- `NANOPI_USER`: SSH user (for example `pi`)
- `NANOPI_SSH_KEY`: private key content (OpenSSH format)
- `NANOPI_APP_DIR`: app directory on NanoPi (for example `/home/pi/stock-assistant-PiPi`)

## 2. Allow Service Restart Without Password

The workflow uses:

- `sudo systemctl restart stock-assistant-api`
- `sudo systemctl restart stock-assistant-discord`

Configure passwordless sudo for these commands on NanoPi:

```bash
sudo visudo
```

Add:

```text
pi ALL=(ALL) NOPASSWD:/bin/systemctl restart stock-assistant-api,/bin/systemctl restart stock-assistant-discord,/bin/systemctl is-active stock-assistant-api,/bin/systemctl is-active stock-assistant-discord
```

If your Linux uses `/usr/bin/systemctl`, adjust the path accordingly.

## 3. First Run

Push to `main` (backend-related paths) or run the workflow manually from GitHub Actions (`workflow_dispatch`).

The workflow will:

1. SSH to NanoPi
2. `git pull --ff-only` in `NANOPI_APP_DIR`
3. install `requirements.txt`
4. restart both services
5. health-check `http://127.0.0.1:8000/health`
