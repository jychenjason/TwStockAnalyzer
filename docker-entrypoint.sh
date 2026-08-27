#!/bin/sh
set -e

export NUMBA_DISABLE_JIT=1

# Decide whether to drop privileges to the non-root appuser.
#
# Under rootless podman the container's UID 0 already maps to the unprivileged
# host user, so files written to the ./data bind mount are host-user-owned.
# We must NOT chown or drop to appuser there — that remaps the volume to an
# inaccessible subordinate UID (e.g. host 100998) and breaks native access.
#
# Under real Docker, UID 0 is real root, so we chown the data volume and drop
# to the non-root appuser (defense in depth, per the deployment plan).
rootless=0
if [ "$(id -u)" = "0" ]; then
    # /proc/self/uid_map row: "<in-ns uid> <host uid> <count>".
    # A host uid other than 0 for in-ns uid 0 means we are rootless.
    host_uid="$(awk 'NR==1{print $2}' /proc/self/uid_map 2>/dev/null)"
    if [ -n "$host_uid" ] && [ "$host_uid" != "0" ]; then
        rootless=1
    fi
fi

run() {
    if [ "$(id -u)" = "0" ] && [ "$rootless" = "0" ]; then
        chown -R appuser:appuser /app/data 2>/dev/null || true
        chmod -R u+rwX /app/data 2>/dev/null || true
        exec su -s /bin/sh appuser -c "$1"
    else
        chmod -R u+rwX /app/data 2>/dev/null || true
        exec sh -c "$1"
    fi
}

if [ "$1" = "streamlit" ]; then
    shift
    # Point at the app module itself.  Handing streamlit the package directory
    # makes it look for streamlit_app.py / __main__.py, neither of which exists,
    # and handing it __init__.py renders a blank page because that module only
    # imports main() without calling it.
    run "exec python -m streamlit run /app/src/twstock_analyzer/streamlit_app/app.py --server.port=8501 --server.address=0.0.0.0 $*"
else
    # Default: CLI commands (analyze, update, report, serve, ...)
    run "exec python -m twstock_analyzer.cli.main $*"
fi
