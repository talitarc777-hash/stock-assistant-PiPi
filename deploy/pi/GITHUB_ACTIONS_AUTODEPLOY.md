# GitHub Actions Auto-Deploy (NanoPi)

This project includes `.github/workflows/deploy-nanopi.yml` to auto-deploy backend changes to NanoPi when `main` is updated.

## 1. Add Repository Secrets

In GitHub repo -> `Settings` -> `Secrets and variables` -> `Actions`, add:

- `NANOPI_HOST`: NanoPi public host/IP
- `NANOPI_PORT`: SSH port (usually `22`)
- `NANOPI_USER`: SSH user (for example `pi`)
- `NANOPI_SSH_KEY`: private key content (OpenSSH format)
- `NANOPI_APP_DIR`: app directory on NanoPi (for example `/home/pi/stock-assistant-PiPi`)

`NANOPI_SSH_KEY` must be the private key, not the `.pub` public key. It should look like:

```text
-----BEGIN OPENSSH PRIVATE KEY-----
...
-----END OPENSSH PRIVATE KEY-----
```

Keep the line breaks when pasting it into GitHub. If GitHub Actions logs
`ssh.ParsePrivateKey: ssh: no key found`, the secret is empty, pasted as one
broken line, or contains the public key instead of the private key.

## 2. Make SSH Reachable From GitHub Actions

This workflow uses SSH from a GitHub-hosted runner to the NanoPi. Your
Cloudflare Tunnel for the backend API only exposes HTTP/HTTPS traffic to the
FastAPI service; it does not make port `22` reachable for this deploy workflow.

Use one of these SSH paths:

- Direct public IP or dynamic DNS with router port forwarding to NanoPi SSH.
- A VPN overlay such as Tailscale with a GitHub Action that joins the tailnet.
- A self-hosted GitHub runner on the NanoPi, so no inbound SSH is needed.
- A separate Cloudflare Access SSH setup, plus a workflow that installs and uses `cloudflared`.

If GitHub Actions logs `dial tcp <host>:<port>: connect: connection refused`,
the runner reached the host but no SSH service is accepting connections on that
port. Check that `NANOPI_HOST` and `NANOPI_PORT` point to SSH, not the public
HTTP API hostname.

## 3. Allow Service Restart Without Password

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

## 4. First Run

Push to `main` (backend-related paths) or run the workflow manually from GitHub Actions (`workflow_dispatch`).

The workflow will:

1. SSH to NanoPi
2. `git pull --ff-only` in `NANOPI_APP_DIR`
3. install `requirements.txt`
4. restart both services
5. health-check `http://127.0.0.1:8000/health`
