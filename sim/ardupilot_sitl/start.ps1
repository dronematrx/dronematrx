# Start two ArduCopter SITL instances (TCP 5760 and 5770) for hardware-bridge tests.
# Usage:  powershell -File sim/ardupilot_sitl/start.ps1        (stop: docker rm -f dronematrx_ardupilot_sitl)
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$bin = Join-Path $here "bin\arducopter"
if (-not (Test-Path $bin)) {
    New-Item -ItemType Directory -Force (Join-Path $here "bin") | Out-Null
    Invoke-WebRequest "https://firmware.ardupilot.org/Copter/stable/SITL_x86_64_linux_gnu/arducopter" -OutFile $bin
    Invoke-WebRequest "https://raw.githubusercontent.com/ArduPilot/ardupilot/master/Tools/autotest/default_params/copter.parm" -OutFile (Join-Path $here "bin\copter.parm")
}
docker rm -f dronematrx_ardupilot_sitl 2>$null | Out-Null
docker run -d --name dronematrx_ardupilot_sitl -p 127.0.0.1:5760:5760 -p 127.0.0.1:5770:5770 `
    -v "${here}:/sitl" --entrypoint sh dronematrx/simhost:dev -c "chmod +x /sitl/bin/arducopter; sh /sitl/run_sitl.sh"
