#!/bin/sh
# Self-extracting script which containse backup for %%PROJECT_NAME%%
#
# project: %%PROJECT_NAME%%
# created: %%TODAY%%

set -e

CHECK="1"
RESTORE="0"
START="0"
BACKUP_TIME="%%TODAY%%"
PROJECT_NAME="%%PROJECT_NAME%%"
ARCHIVE="$0"

while :; do
    case $1 in
        -h|-\?|--help)   # Call a "show_help" function to display a synopsis, then exit.
            echo Decrypt and unpack backup
            echo Project: "$PROJECT_NAME"
            echo Created: "$BACKUP_TIME"
            echo Usage:
            echo ""
            echo "$0 [--restore/-r] [-s/--start] [-h/--help] [passphrase]"
            echo ""
            echo "   passphrase      Key to decrypt archive. Can be set by env PASSPHRASE"
            echo ""
            echo "  -h, --help       Show this help"
            echo "  -r, --restore    Automatically restore project after unpacking"
            echo "  -s, --start      Automatically start project after unpacking. Implicitly enables --restore"
            echo ""
            exit
            ;;
        -r|--restore)
            RESTORE="1"
            ;;
        -s|--start)
            RESTORE="1"
            START="1"
            ;;
        --)
            shift
            break
            ;;
        -?*)
            printf 'WARN: Unknown option (ignored): %s\n' "$1" >&2
            ;;
        *)
            PASSPHRASE="${PASSPHRASE:-$1}"
            break
    esac

    shift
done


check_command() {
    if ! command -v "$1" 2>&1 > /dev/null
    then
        echo "- '$1' could not be found"
        CHECK="0"
    fi
}

check_command tar
check_command gpg
check_command gzip
check_command sed

if [ "$CHECK" = "0" ]; then
    echo "Preconditions failed"
    exit 1
fi

if [ "1$PASSPHRASE" = "1" ]; then
    echo "Decryption key is not set via argument neither via environment variable PASSPHRASE"
    exit 2
fi

echo "decrypting and unpacking to $PROJECT_NAME"
sed '0,/^#EOF#$/d' "$ARCHIVE" | gpg --batch --yes --passphrase "$PASSPHRASE" --output - --decrypt - | tar zx

if [ "$RESTORE" = "1" ]; then
    echo "restoring..."
    export START_AFTER_RESTORE="$START"
    exec "./$PROJECT_NAME/restore.sh"
else
    echo "unpacking complete; use the command bellow to restore project"
    echo ""
    echo "    ./$PROJECT_NAME/restore.sh"
    echo ""
fi

echo "done"
exit 0
#EOF#
