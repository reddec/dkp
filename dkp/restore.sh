#!/bin/sh
set -e
cd "$(dirname -- "$0")"

START_AFTER_RESTORE="${START_AFTER_RESTORE:-0}"

if [ -d images ]; then
    echo "Restoring images..."
    find images -name '*.tar' -print -exec docker image load -i {} \;
fi

if [ -d volumes ]; then
    echo "Restoring volumes..."
    find volumes -name '*.tar' | while read file; do
        VOLUME="$(basename "$file" .tar)"
        docker volume create "$VOLUME"
        echo "restoring $VOLUME"
        docker run --rm -v "$(pwd)/$file:/input.tar:ro" -v "$VOLUME:/output" busybox tar -C /output -xf /input.tar
    done
fi

if [ "$START_AFTER_RESTORE" = "1" ]; then
    echo "Starting..."
    cd "project/%%PROJECT_NAME%%"
    docker compose -p "%%PROJECT_NAME%%" %%SOURCE_ARGS%% up -d
else
    echo "Restore complete. Use command bellow to start project"
    echo ""
    echo "    cd project/%%PROJECT_NAME%%"
    echo "   " docker compose %%SOURCE_ARGS%% up -d
    echo ""
fi