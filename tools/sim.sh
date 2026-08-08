#!/usr/bin/env bash
# Run Edgewise in the EMF badge simulator. Needs the sim checked out next door:
#   git clone https://github.com/emfcamp/badge-2024-software ../badge-2024-software
set -euo pipefail

APP="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SIM="${SIM_DIR:-$APP/../badge-2024-software/sim}"

if [ ! -f "$SIM/run.py" ]; then
    echo "simulator not found at $SIM (clone badge-2024-software next door, or set SIM_DIR)" >&2
    exit 1
fi

# The sim's override launcher trips a circular import; pre-import the scheduler.
# The marker matches the *inserted line*, not this app's name: more than one
# Tildagon app shares a single badge-2024-software checkout here, and an
# app-specific marker means the second app patches an already-patched file.
grep -q "import system.scheduler  # sim-fix" "$SIM/run.py" || \
    sed -i '/^def replace_launcher/a\    import system.scheduler  # sim-fix' "$SIM/run.py"

# The simulator ships no umqtt at all, so without this shim the whole MQTT path
# -- connect, retained rebuild, availability, the ack round trip -- can only be
# tested on hardware. Dev-only; a badge uses the real frozen umqtt.simple.
cp -r "$APP/tools/simshim/umqtt" "$SIM/fakes/"

ln -sfn "$APP" "$SIM/apps/edgewise"
exec python3 "$SIM/run.py" edgewise.EdgewiseApp
