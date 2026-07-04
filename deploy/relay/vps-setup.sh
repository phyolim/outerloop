#!/usr/bin/env bash
# One-time setup for the PUBLIC relay box (AWS Lightsail / EC2, Ubuntu 22.04+).
# This box does two jobs and nothing else:
#   1. accept the mini's outbound `ssh -R` reverse tunnel on loopback:8765
#   2. run Caddy on :443 — auto-HTTPS + basic-auth for the browser UI, Bearer for /api
#
# Run it ONCE, as root (sudo), on a fresh box. The dashboard password must come from
# the env (DASH_PASSWORD) or a 3rd arg so the piped form stays non-interactive:
#   curl -fsSL .../vps-setup.sh | DASH_PASSWORD='s3cret' sudo -E bash -s -- <DASHBOARD_USER> <MINI_SSH_PUBKEY>
# or copy it over and:
#   sudo DASH_PASSWORD='s3cret' bash vps-setup.sh <DASHBOARD_USER> "<MINI_SSH_PUBKEY>"
#   sudo bash vps-setup.sh <DASHBOARD_USER> "<MINI_SSH_PUBKEY>" 's3cret'
# Omit the password entirely only in a real terminal — then caddy prompts for it.
#
# Prereqs you do in the AWS console first (can't be scripted from inside the box):
#   - open inbound 22, 80, 443 in the instance's security group / Lightsail firewall
#   - note the box's PUBLIC IP (the script derives the <ip>.sslip.io hostname from it)
set -euo pipefail

DASH_USER="${1:?usage: vps-setup.sh <dashboard-username> <mini-ssh-public-key>}"
MINI_PUBKEY="${2:?need the mini SSH public key (~/.ssh/id_ed25519.pub on the mini)}"

PUB_IP="$(curl -fsS https://checkip.amazonaws.com | tr -d '[:space:]')"
HOSTNAME="${PUB_IP}.sslip.io"   # resolves to PUB_IP, lets Caddy get a real Let's Encrypt cert — no domain to buy

echo ">> relay hostname will be: https://${HOSTNAME}"

# --- 1. the unprivileged tunnel user: port-forwarding ONLY, no shell, no commands ---
if ! id tunnel &>/dev/null; then
  useradd -m -s /usr/sbin/nologin tunnel
fi
install -d -m 700 -o tunnel -g tunnel /home/tunnel/.ssh
# `restrict` drops everything; we add back only port-forwarding. A stolen key from the
# mini can forward ports and nothing else — no shell on the relay.
echo "restrict,port-forwarding ${MINI_PUBKEY}" > /home/tunnel/.ssh/authorized_keys
chown tunnel:tunnel /home/tunnel/.ssh/authorized_keys
chmod 600 /home/tunnel/.ssh/authorized_keys

# --- 1b. sshd keepalive: reap dead tunnels fast so :8765 is free on reconnect ---
# Without this a dropped `ssh -R` can leave 8765 held; the mini's next dial fails
# (ExitOnForwardFailure). ClientAlive* probes let sshd notice and release it.
install -d -m 755 /etc/ssh/sshd_config.d
cat > /etc/ssh/sshd_config.d/10-loop-tunnel.conf <<'EOF'
# Managed by deploy/relay/vps-setup.sh — reap dead reverse tunnels quickly.
ClientAliveInterval 30
ClientAliveCountMax 2
TCPKeepAlive yes
EOF
# Reload whichever unit this distro ships (Ubuntu 22.04 = ssh, some = sshd).
systemctl reload ssh 2>/dev/null || systemctl reload sshd 2>/dev/null || true

# --- 2. Caddy (official apt repo) ---
if ! command -v caddy &>/dev/null; then
  apt-get update -y
  apt-get install -y debian-keyring debian-archive-keyring apt-transport-https curl
  curl -fsSL https://dl.cloudsmith.io/public/caddy/stable/gpg.key \
    | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
  curl -fsSL https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt \
    > /etc/apt/sources.list.d/caddy-stable.list
  apt-get update -y
  apt-get install -y caddy
fi

# --- 3. dashboard password -> bcrypt hash for Caddy basic-auth ---
# Password from 3rd arg or $DASH_PASSWORD -> hash non-interactively (keeps curl|bash working).
# Only if neither is set do we fall back to caddy's interactive twice-typed prompt.
DASH_PASSWORD="${3:-${DASH_PASSWORD:-}}"
if [ -n "${DASH_PASSWORD}" ]; then
  HASH="$(caddy hash-password --plaintext "${DASH_PASSWORD}")"
else
  echo ">> set the DASHBOARD password (what you'll type in the browser login box):"
  HASH="$(caddy hash-password)"   # prompts twice, prints a bcrypt hash
fi

# --- 4. Caddyfile: TLS at the edge; /api -> Bearer (app), everything else -> basic-auth ---
# The basic-auth directive was renamed `basicauth` -> `basic_auth` in Caddy 2.8.
# Pick the name that matches the installed caddy; `caddy validate` below is the safety net.
write_caddyfile() {   # $1 = directive name
	cat > /etc/caddy/Caddyfile <<EOF
# Auto-HTTPS for ${HOSTNAME} (HTTP-01 challenge, needs :80 + :443 open).
${HOSTNAME} {
	# Worker API: no basic-auth here — the app already checks per-device Bearer tokens.
	@api path /api/*
	handle @api {
		reverse_proxy localhost:8765
	}
	# Browser UI: one shared login. This is the ONLY guard on the merge-approving UI.
	handle {
		$1 {
			${DASH_USER} ${HASH}
		}
		reverse_proxy localhost:8765
	}
}
EOF
}

# caddy version -> "v2.8.4 ..."; >=2.8 gets basic_auth, older gets basicauth.
CADDY_VER="$(caddy version 2>/dev/null | grep -oE 'v?[0-9]+\.[0-9]+' | head -n1 | tr -d v)"
CADDY_MINOR="${CADDY_VER#*.}"
if [ "${CADDY_VER%%.*}" = "2" ] && [ "${CADDY_MINOR:-0}" -lt 8 ] 2>/dev/null; then
	DIRECTIVE=basicauth
else
	DIRECTIVE=basic_auth
fi
write_caddyfile "${DIRECTIVE}"
# Safety net: if the guessed directive doesn't validate, try the other spelling.
if ! caddy validate --config /etc/caddy/Caddyfile >/dev/null 2>&1; then
	[ "${DIRECTIVE}" = "basic_auth" ] && DIRECTIVE=basicauth || DIRECTIVE=basic_auth
	write_caddyfile "${DIRECTIVE}"
	caddy validate --config /etc/caddy/Caddyfile   # fail loudly if still broken
fi

systemctl reload caddy || systemctl restart caddy

cat <<EOF

==================================================================
 relay is up.
   dashboard URL : https://${HOSTNAME}/
   workers point : INBOX_HUB=https://${HOSTNAME}
 Put these into the Mac .pkg build (deploy/mac/build-pkg.sh):
   VPS_HOST=${HOSTNAME}
   TUNNEL_USER=tunnel
 The mini reaches this box via its outbound ssh -R; nothing inbound to your home.
==================================================================
EOF
