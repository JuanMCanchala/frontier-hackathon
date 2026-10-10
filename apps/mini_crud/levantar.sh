#!/bin/sh
set -eu

if [ -z "${VNC_PASSWORD:-}" ]; then
  echo "VNC_PASSWORD must be set in the environment" >&2
  exit 1
fi

docker run --rm -d \
  -p 6080:6080 \
  -e "VNC_PASSWORD=${VNC_PASSWORD}" \
  mini-crud:local
