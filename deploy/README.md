# Public VPS deployment

This deployment serves the frontend and API on one HTTPS domain. It also runs
the API directly on the VPS so its Docker client can access the host Docker
daemon for the restricted C output-test sandbox.

## Requirements

- An Ubuntu 24.04 VPS with a public IPv4 address.
- A domain name whose DNS `A` record points to that address.
- Inbound TCP ports 80 and 443 allowed by the VPS firewall.

Do not use a static-only host for this setup: it cannot run the API or C
sandbox. The `docker` group grants root-equivalent access to the host. Keep the
VPS patched, limit access to its SSH account, and do not give shell access to
untrusted users.

## Install

SSH into the VPS, then install the runtime dependencies:

```sh
sudo apt update
sudo apt install -y git python3-venv gcc caddy docker.io
sudo systemctl enable --now docker caddy
sudo useradd --system --create-home --home-dir /var/lib/bug-hunter --shell /usr/sbin/nologin bughunter
sudo usermod -aG docker bughunter
sudo install -d -o bughunter -g bughunter /opt/bug-hunter
sudo install -d -o bughunter -g bughunter /var/lib/bug-hunter
sudo -u bughunter git clone https://github.com/manojprabhakar6/bughunter2.O.git /opt/bug-hunter
```

Create the virtual environment and install production dependencies:

```sh
sudo -u bughunter python3 -m venv /opt/bug-hunter/.venv
sudo -u bughunter /opt/bug-hunter/.venv/bin/pip install -r /opt/bug-hunter/backend/requirements.txt
```

Build the isolated C sandbox image on the VPS:

```sh
sudo docker build --tag bug-hunter-c-sandbox:1 --file /opt/bug-hunter/backend/sandbox/Dockerfile /opt/bug-hunter/backend/sandbox
```

Install the included service and Caddy configuration. Replace
`your-domain.example` with the domain pointed to your VPS:

```sh
sudo cp /opt/bug-hunter/deploy/bug-hunter.service /etc/systemd/system/bug-hunter.service
sudo cp /opt/bug-hunter/deploy/Caddyfile /etc/caddy/Caddyfile
sudo nano /etc/caddy/Caddyfile
sudo systemctl daemon-reload
sudo systemctl enable --now bug-hunter
sudo caddy validate --config /etc/caddy/Caddyfile
sudo systemctl reload caddy
```

Caddy provisions HTTPS automatically after DNS is correct and ports 80 and 443
are reachable. The frontend uses the same domain for `/api`, which Caddy
proxies to the API bound to loopback. SQLite data persists in
`/var/lib/bug-hunter/bug_hunter.sqlite3`.

Open `https://your-domain.example/login` after deployment. Do not use
`bug-hunter.localhost` on other devices; `.localhost` always refers to the
device making the request.

## Updating

```sh
sudo -u bughunter git -C /opt/bug-hunter pull
sudo -u bughunter /opt/bug-hunter/.venv/bin/pip install -r /opt/bug-hunter/backend/requirements.txt
sudo docker build --tag bug-hunter-c-sandbox:1 --file /opt/bug-hunter/backend/sandbox/Dockerfile /opt/bug-hunter/backend/sandbox
sudo systemctl restart bug-hunter
```

Back up `/var/lib/bug-hunter/bug_hunter.sqlite3` regularly. Public deployment of
an application that accepts user-submitted source needs ongoing monitoring and
security maintenance; the C container limits are not a substitute for
dedicated multi-tenant sandbox infrastructure.
