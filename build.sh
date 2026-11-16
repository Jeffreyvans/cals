#!/usr/bin/env bash
# Render runs this on every deploy, before the web process starts.
set -o errexit
set -o nounset
set -o pipefail

pip install --upgrade pip
pip install -r requirements.txt

python manage.py collectstatic --noinput
python manage.py migrate --noinput

# Create or repair the main platform administrator. This is idempotent and
# does nothing when PLATFORM_ADMIN_USERNAME / _EMAIL / _PASSWORD are not set.
python manage.py ensure_platform_admin
