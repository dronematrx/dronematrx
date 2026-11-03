# Start Drone MatrX on this laptop with one command:
#
#   powershell -ExecutionPolicy Bypass -File start-dronematrx.ps1              (real drones + video)
#   powershell -ExecutionPolicy Bypass -File start-dronematrx.ps1 -Simulation  (also the Gazebo/PX4 simulator)
#   powershell -ExecutionPolicy Bypass -File start-dronematrx.ps1 -DryRun      (show what it would do)
#
# It finds this laptop's network address by itself (the video wall needs it),
# starts the database, NATS and MediaMTX in Docker, applies database migrations,
# opens the backend and the website in their own windows, waits until both
# answer, and opens Auto Mission in the browser.
param([switch]$Simulation, [switch]$DryRun)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path

function Step([string]$msg) { Write-Host "==> $msg" -ForegroundColor Cyan }

function Get-LanAddress {
    # The IPv4 address of the interface that carries the default route (Wi-Fi or
    # Ethernet), ignoring Docker/WSL/VPN virtual adapters.
    $route = Get-NetRoute -DestinationPrefix "0.0.0.0/0" -ErrorAction SilentlyContinue |
        Sort-Object { $_.RouteMetric + $_.InterfaceMetric } | Select-Object -First 1
    if ($route) {
        $ip = Get-NetIPAddress -InterfaceIndex $route.InterfaceIndex -AddressFamily IPv4 -ErrorAction SilentlyContinue |
            Where-Object { $_.IPAddress -notlike "169.254.*" } | Select-Object -First 1
        if ($ip) { return $ip.IPAddress }
    }
    return "127.0.0.1"
}

function Wait-Http([string]$url, [int]$seconds) {
    $deadline = (Get-Date).AddSeconds($seconds)
    while ((Get-Date) -lt $deadline) {
        try { Invoke-WebRequest $url -UseBasicParsing -TimeoutSec 3 | Out-Null; return $true } catch { Start-Sleep 1 }
    }
    return $false
}

$ip = Get-LanAddress
Step "Laptop address for video: $ip"
if ($DryRun) {
    Write-Host "Would: docker compose up db nats mediamtx$(if ($Simulation) { ' simhost' }) (MEDIAMTX_WEBRTC_HOSTS=$ip)"
    Write-Host "Would: alembic upgrade head; start backend (port 8000) and website (port 5173); open http://localhost:5173/auto-mission"
    exit 0
}

Step "Checking Docker"
docker info *> $null
if ($LASTEXITCODE -ne 0) { throw "Docker Desktop is not running. Start it, wait until it says 'running', then run this again." }

Step "Starting database, NATS and video server"
$env:MEDIAMTX_WEBRTC_HOSTS = $ip
$lastIpFile = Join-Path $root ".dmx-last-ip"
$recreate = @()
if (-not (Test-Path $lastIpFile) -or (Get-Content $lastIpFile -ErrorAction SilentlyContinue) -ne $ip) { $recreate = @("--force-recreate") }
$services = @("db", "nats", "mediamtx") + $(if ($Simulation) { @("simhost") } else { @() })
Push-Location $root
try {
    docker compose -f docker-compose.yml -f docker-compose.sim.yml up -d @recreate @services
    if ($LASTEXITCODE -ne 0) { throw "docker compose failed" }
} finally { Pop-Location }
Set-Content -Path $lastIpFile -Value $ip

Step "Applying database migrations"
Push-Location (Join-Path $root "backend")
try {
    & .\venv\Scripts\alembic upgrade head
    if ($LASTEXITCODE -ne 0) { throw "Database migration failed" }
} finally { Pop-Location }

Step "Starting the backend (window 'Drone MatrX backend')"
if (-not (Wait-Http "http://127.0.0.1:8000/health" 1)) {
    Start-Process powershell -WorkingDirectory (Join-Path $root "backend") -ArgumentList @(
        "-NoExit", "-Command", "`$Host.UI.RawUI.WindowTitle='Drone MatrX backend'; .\venv\Scripts\python -m uvicorn app.main:app --port 8000")
}
Step "Starting the website (window 'Drone MatrX website')"
if (-not (Wait-Http "http://localhost:5173" 1)) {
    Start-Process powershell -WorkingDirectory (Join-Path $root "frontend") -ArgumentList @(
        "-NoExit", "-Command", "`$Host.UI.RawUI.WindowTitle='Drone MatrX website'; npm run dev -- --port 5173")
}

if (-not (Wait-Http "http://127.0.0.1:8000/health" 90)) { throw "The backend did not start: look at the 'Drone MatrX backend' window." }
if (-not (Wait-Http "http://localhost:5173" 60)) { throw "The website did not start: look at the 'Drone MatrX website' window." }
Step "Ready. Opening Auto Mission. (Real drones: open Drone Setup and press Start service.)"
Start-Process "http://localhost:5173/auto-mission"
