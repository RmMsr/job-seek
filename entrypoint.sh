#!/bin/sh
set -eu

mkdir --parents data/browser-profile
if [ ! -f data/config.toml ]; then
    cp defaults/config.toml data/config.toml
fi

exec "$@"
