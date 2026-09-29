#!/usr/bin/env bash
set -o errexit

pip install -r requirements.txt
pip install gunicorn==23.0.0

python manage.py collectstatic --no-input
python manage.py migrate