#!/usr/bin/env bash
# Midway Scout — one-shot setup for a fresh Ubuntu 22.04/24.04 EC2 box.
set -euo pipefail
cd "$(dirname "$0")"

echo "==> Installing system packages…"
# LOCAL PATCH (not in the upstream zip): without a noninteractive frontend, apt
# stops on debconf prompts (e.g. the "pending kernel upgrade" dialog) and the
# whole script dies on a box with no usable terminal.
export DEBIAN_FRONTEND=noninteractive
sudo -E apt-get update -y
sudo -E apt-get install -y -o Dpkg::Options::=--force-confold \
  python3 python3-venv python3-pip unzip

# Prefer 3.12: very new Pythons (3.13/3.14) can lack prebuilt wheels for
# browser-use/playwright/trafilatura, forcing slow or failing source builds.
PY="$(command -v python3.12 || command -v python3.11 || command -v python3)"
echo "==> Using $($PY --version)"

echo "==> Creating virtualenv…"
"$PY" -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt

# Chromium (browser engine) needs real memory; skip it on small instances.
TOTAL_MB=$(free -m | awk '/^Mem:/{print $2}')
if [ "${TOTAL_MB:-0}" -lt 3500 ]; then
  echo "==> ${TOTAL_MB}MB RAM detected — skipping Chromium (browser engine unusable"
  echo "    on this instance size; the rss and firecrawl engines are unaffected)."
else
  echo "==> Installing headless Chromium for browser-use…"
  python -m playwright install --with-deps chromium || \
    python -m patchright install --with-deps chromium || \
    echo "WARN: Chromium install failed — rss/firecrawl engines still work."
fi

if [ ! -f .env ]; then
  cp .env.example .env
  echo ""
  echo "==> Created .env — EDIT IT NOW and add your API keys:"
  echo "    nano .env"
fi

echo ""
echo "==> Done. Start the server with:  ./run.sh"
echo "    Then open http://<EC2-public-IP>:8000  (open port 8000 in the security group)"
