# Run Edgewise in the EMF badge simulator on Windows. Needs the sim next door:
#   git clone https://github.com/emfcamp/badge-2024-software ..\badge-2024-software
# Windows symlinks need developer mode or admin, so this copies instead.
$ErrorActionPreference = 'Stop'

$app = Split-Path -Parent $PSScriptRoot
$sim = if ($env:SIM_DIR) { $env:SIM_DIR } else { Join-Path (Split-Path -Parent $app) 'badge-2024-software\sim' }

if (-not (Test-Path (Join-Path $sim 'run.py'))) {
    Write-Error "simulator not found at $sim (clone badge-2024-software next door, or set SIM_DIR)"
}

# The sim's override launcher trips a circular import; pre-import the scheduler.
# The marker matches the *inserted line*, not this app's name: more than one
# Tildagon app shares a single badge-2024-software checkout here, and an
# app-specific marker means the second app patches an already-patched file.
$runPath = Join-Path $sim 'run.py'
$run = Get-Content $runPath -Raw
if ($run -notmatch 'import system\.scheduler  # sim-fix') {
    $run = $run -replace '(?m)^def replace_launcher\(module_name: str, class_name: str\):\r?\n',
        "def replace_launcher(module_name: str, class_name: str):`n    import system.scheduler  # sim-fix`n"
    # WriteAllText with UTF8Encoding($false), never Set-Content -Encoding utf8:
    # PowerShell 5.1 adds a BOM, and a BOM in a file a strict parser reads is
    # how a release once got rejected by the app store.
    [IO.File]::WriteAllText($runPath, $run, (New-Object System.Text.UTF8Encoding($false)))
}

$dest = Join-Path $sim 'apps\edgewise'
New-Item -ItemType Directory -Force -Path $dest | Out-Null
Copy-Item (Join-Path $app '*.py') $dest -Force
Copy-Item (Join-Path $app 'tildagon.toml') $dest -Force
# Copy-Item with a wildcard does not recurse, so the board profiles need their
# own line or the app starts with no LED map at all.
Copy-Item (Join-Path $app 'boards') $dest -Recurse -Force

# The simulator ships no umqtt at all, so without this shim the whole MQTT path
# -- connect, retained rebuild, availability, the ack round trip -- can only be
# tested on hardware. Dev-only; a badge uses the real frozen umqtt.simple.
Copy-Item (Join-Path $PSScriptRoot 'simshim\umqtt') (Join-Path $sim 'fakes') -Recurse -Force

Push-Location $sim
try { python run.py edgewise.EdgewiseApp } finally { Pop-Location }
