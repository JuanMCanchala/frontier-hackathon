#!/bin/sh
set -eu

if [ -z "${VNC_PASSWORD:-}" ]; then
  echo "VNC_PASSWORD must be set" >&2
  exit 1
fi

export DISPLAY=:1
export PYTHONPATH=/app

python3 -m mini_crud.server &
http_pid=$!

Xvfb :1 -screen 0 1280x800x24 &
xvfb_pid=$!
sleep 1

x11vnc -storepasswd "$VNC_PASSWORD" /tmp/vncpass
chmod 600 /tmp/vncpass

chromium \
  --no-sandbox \
  --disable-dev-shm-usage \
  --disable-gpu \
  --kiosk \
  --window-position=0,0 \
  --window-size=1280,800 \
  --lang=es \
  --disable-features=Translate,TranslateUI \
  --app="http://127.0.0.1:8765/" &

x11vnc \
  -display :1 \
  -rfbauth /tmp/vncpass \
  -forever \
  -shared \
  -noxdamage \
  -rfbport 5900 \
  -listen 127.0.0.1 &

exec websockify --web=/usr/share/novnc/ 0.0.0.0:6080 127.0.0.1:5900
