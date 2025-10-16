#!/usr/bin/sh

cd /root/stream-viewer
exec /usr/bin/python3 -m src.stream_manager -c /root/.config/streams.json