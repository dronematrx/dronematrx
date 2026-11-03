# Start two ArduCopter SITL instances (TCP 5760 and 5770) for hardware-bridge tests.
# Usage:  powershell -File sim/ardupilot_sitl/start.ps1        (stop: docker rm -f dronematrx_ardupilot_sitl)
# Then:   backend\venv\Scripts\python -m pytest hwbridge/tests/test_ardupilot_sitl.py -v
$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$binDir = Join-Path $here "bin"
$bin = Join-Path $binDir "arducopter"
New-Item -ItemType Directory -Force $binDir | Out-Null

function Get-Verified([string]$url, [string]$dest, [int]$minBytes) {
    # Download to a temporary file and move it into place only when complete,
    # so an interrupted download never leaves a truncated binary behind.
    $part = "$dest.part"
    Invoke-WebRequest $url -OutFile $part
    if ((Get-Item $part).Length -lt $minBytes) { Remove-Item $part; throw "Download of $url is incomplete" }
    Move-Item -Force $part $dest
}

# A complete ArduCopter SITL build is ~7 MB; anything smaller is a broken download.
if (-not (Test-Path $bin) -or (Get-Item $bin).Length -lt 6MB) {
    Get-Verified "https://firmware.ardupilot.org/Copter/stable/SITL_x86_64_linux_gnu/arducopter" $bin 6MB
}
if (-not (Test-Path (Join-Path $binDir "copter.parm"))) {
    Get-Verified "https://raw.githubusercontent.com/ArduPilot/ardupilot/master/Tools/autotest/default_params/copter.parm" (Join-Path $binDir "copter.parm") 500
}
docker rm -f dronematrx_ardupilot_sitl 2>$null | Out-Null
docker run -d --name dronematrx_ardupilot_sitl -p 127.0.0.1:5760:5760 -p 127.0.0.1:5770:5770 `
    -v "${here}:/sitl" --entrypoint sh dronematrx/simhost:dev -c "chmod +x /sitl/bin/arducopter; sh /sitl/run_sitl.sh"
Write-Host "ArduPilot SITL starting on tcp:127.0.0.1:5760 (system id 1) and tcp:127.0.0.1:5770 (system id 2)."
