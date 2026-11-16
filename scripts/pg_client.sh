#!/bin/sh
set -eu
umask 077
# Runtime authentication only. Never print the password or export it as PGPASSWORD.
# Escape the two special pgpass characters; the temporary file lives on tmpfs.
if [ ! -r "$PGPASSWORD_FILE" ]; then
    printf '%s\n' 'Required runtime password file is unavailable' >&2
    exit 1
fi
password=$(cat "$PGPASSWORD_FILE")
password=$(printf '%s' "$password" | tr -d '\r\n' | sed -e 's/\\/\\\\/g' -e 's/:/\\:/g')
if [ -z "$password" ]; then
    printf '%s\n' 'Runtime password file is empty' >&2
    exit 1
fi
export PGPASSFILE=/tmp/canopy-client.pgpass
printf '%s:%s:%s:%s:%s\n' "$PGHOST" "${PGPORT:-5432}" "$PGDATABASE" "$PGUSER" "$password" > "$PGPASSFILE"
unset password
exec "$@"
