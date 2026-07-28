<#
.SYNOPSIS
    Boot the whole Loom system with one command.

.DESCRIPTION
    Runs the sequence documented in README.md Quickstart, in order, and stops at the
    first step that fails:

      1. check .venv, .env and node_modules
      2. load .env into this process (nothing here reads it automatically)
      3. docker compose up -d, then wait for Postgres / Redis / Qdrant
      4. python scripts/init_db.py          (schema)
      5. python -m demo.shopping_assistant.seed  (shop data; safe to re-run)
      6. start the API on :8000 via scripts/serve.py, wait for /health
      7. start the Vite dev server on :5173 and open the browser

    Both servers share this console, so their logs interleave here. Ctrl+C stops both.
    The Docker services are left running on purpose; stop them with `docker compose down`.

.PARAMETER NoFrontend
    Backend only. Nothing is started on :5173 and no browser opens.

.PARAMETER SkipSeed
    Skip init_db.py and the seed. Use for a fast restart when the database is already
    set up; Docker is still brought up because the API needs it.

.PARAMETER NoBrowser
    Start everything but do not open a browser window.

.PARAMETER Port
    Port for the API (default 8000). The frontend expects 8000 unless VITE_API_URL is set.

.PARAMETER ReadyTimeoutSec
    How long to wait for the API's first /health answer (default 300). Startup is bounded
    by the first Postgres connection, which can take over two minutes on some Windows
    machines; the wait prints progress so a slow boot is distinguishable from a hang.

.EXAMPLE
    .\run.ps1
.EXAMPLE
    .\run.ps1 -SkipSeed -NoBrowser
.EXAMPLE
    .\run.ps1 -NoFrontend
#>
[CmdletBinding()]
param(
    [switch]$NoFrontend,
    [switch]$SkipSeed,
    [switch]$NoBrowser,
    [int]$Port = 8000,
    [int]$ReadyTimeoutSec = 300
)

$ErrorActionPreference = 'Stop'

$Root = $PSScriptRoot
$FrontendPort = 5173

# ---------------------------------------------------------------- output helpers

$script:Step = 0

function Write-Step {
    param([string]$Message)
    $script:Step++
    Write-Host ""
    Write-Host ("[{0}] {1}" -f $script:Step, $Message) -ForegroundColor Cyan
}

function Write-Ok {
    param([string]$Message)
    Write-Host ("    ok  " + $Message) -ForegroundColor Green
}

function Write-Note {
    param([string]$Message)
    Write-Host ("    " + $Message) -ForegroundColor DarkGray
}

function Write-Warn {
    param([string]$Message)
    Write-Host ("    !   " + $Message) -ForegroundColor Yellow
}

function Stop-Run {
    # Fail with an actionable message instead of a stack trace.
    param([string]$Message, [string[]]$Fix = @())
    Write-Host ""
    Write-Host ("ERROR: " + $Message) -ForegroundColor Red
    foreach ($line in $Fix) { Write-Host ("       " + $line) -ForegroundColor Yellow }
    Write-Host ""
    exit 1
}

# ---------------------------------------------------------------- wait helpers

function Test-TcpPort {
    param([string]$TargetHost, [int]$TargetPort)
    $client = New-Object System.Net.Sockets.TcpClient
    try {
        $client.Connect($TargetHost, $TargetPort)
        return $true
    } catch {
        return $false
    } finally {
        $client.Dispose()
    }
}

function Wait-ContainerHealthy {
    # Polls the container's own healthcheck (defined in docker-compose.yml).
    param([string]$Container, [int]$TimeoutSec = 120)
    $deadline = (Get-Date).AddSeconds($TimeoutSec)
    while ((Get-Date) -lt $deadline) {
        $status = & docker inspect -f '{{.State.Health.Status}}' $Container 2>$null
        if ($LASTEXITCODE -eq 0 -and $status -eq 'healthy') { return $true }
        Start-Sleep -Seconds 1
    }
    return $false
}

function Wait-TcpPort {
    # Qdrant declares no healthcheck, so readiness is "the REST port accepts a connection".
    param([string]$TargetHost, [int]$TargetPort, [int]$TimeoutSec = 60)
    $deadline = (Get-Date).AddSeconds($TimeoutSec)
    while ((Get-Date) -lt $deadline) {
        if (Test-TcpPort -TargetHost $TargetHost -TargetPort $TargetPort) { return $true }
        Start-Sleep -Seconds 1
    }
    return $false
}

function Stop-Tree {
    # Stop-Process kills only the launcher (npm.cmd), orphaning node/vite underneath,
    # so kill the whole tree by PID.
    param($Process)
    if ($null -eq $Process) { return }
    if ($Process.HasExited) { return }
    & taskkill /PID $Process.Id /T /F 2>$null | Out-Null
}

# ---------------------------------------------------------------- 1. prerequisites

Push-Location $Root
try {

Write-Host ""
Write-Host "Loom - e-commerce AI assistant" -ForegroundColor White
Write-Host ("root: " + $Root) -ForegroundColor DarkGray

Write-Step "Checking prerequisites"

$Python = Join-Path $Root '.venv\Scripts\python.exe'
if (-not (Test-Path $Python)) {
    Stop-Run "No virtual environment at .venv\" @(
        "python -m venv .venv",
        ".venv\Scripts\activate",
        "pip install -r requirements-dev.txt"
    )
}
Write-Ok ".venv"

if (-not (Test-Path (Join-Path $Root '.env'))) {
    Copy-Item (Join-Path $Root '.env.example') (Join-Path $Root '.env')
    Stop-Run "No .env - copied .env.example to .env for you" @(
        "Open .env and set a provider key:",
        "  ANTHROPIC_API_KEY=...            (DEFAULT_PROVIDER=anthropic)",
        "  OPENAI_API_KEY=...               (DEFAULT_PROVIDER=openai)",
        "Then run this script again."
    )
}
Write-Ok ".env"

$Npm = $null
if (-not $NoFrontend) {
    $npmCommand = Get-Command npm -ErrorAction SilentlyContinue
    if ($null -eq $npmCommand) {
        Stop-Run "npm is not on PATH (Node.js 20.19+ required for the UI)" @(
            "Install Node.js, or run backend only:  .\run.ps1 -NoFrontend"
        )
    }
    $Npm = $npmCommand.Source
    if (-not (Test-Path (Join-Path $Root 'frontend\node_modules'))) {
        Stop-Run "frontend\node_modules is missing" @(
            "cd frontend; npm install",
            "Or run backend only:  .\run.ps1 -NoFrontend"
        )
    }
    Write-Ok "node + frontend\node_modules"
}

$dockerCommand = Get-Command docker -ErrorAction SilentlyContinue
if ($null -eq $dockerCommand) {
    Stop-Run "docker is not on PATH" @("Install Docker Desktop and start it.")
}
& docker info 2>$null | Out-Null
if ($LASTEXITCODE -ne 0) {
    Stop-Run "Docker is installed but not running" @("Start Docker Desktop, then run this script again.")
}
Write-Ok "docker daemon"

# ---------------------------------------------------------------- 2. environment

Write-Step "Loading .env"

# Settings.from_env() reads os.environ only - nothing in the app loads .env by itself,
# so export it here. Child processes inherit these.
Get-Content (Join-Path $Root '.env') | ForEach-Object {
    $line = $_.Trim()
    if ($line -and -not $line.StartsWith('#')) {
        $split = $line.IndexOf('=')
        if ($split -gt 0) {
            $key = $line.Substring(0, $split).Trim()
            $value = $line.Substring($split + 1).Trim().Trim('"').Trim("'")
            [Environment]::SetEnvironmentVariable($key, $value, 'Process')
        }
    }
}

$provider = $env:DEFAULT_PROVIDER
if (-not $provider) { $provider = 'anthropic' }
$keyName = 'ANTHROPIC_API_KEY'
if ($provider -eq 'openai') { $keyName = 'OPENAI_API_KEY' }
if ($provider -eq 'google') { $keyName = 'GOOGLE_API_KEY' }
if (-not [Environment]::GetEnvironmentVariable($keyName, 'Process')) {
    Write-Warn ("DEFAULT_PROVIDER=" + $provider + " but " + $keyName + " is empty in .env.")
    Write-Warn "The UI and storefront will work; every chat turn will fail."
} else {
    Write-Ok ("provider: " + $provider)
}

# ---------------------------------------------------------------- 3. docker services

Write-Step "Starting Docker services (Postgres :5433, Redis :6379, Qdrant :6333)"

& docker compose up -d
if ($LASTEXITCODE -ne 0) {
    Stop-Run "docker compose up failed" @("Check the output above, then run this script again.")
}

if (-not (Wait-ContainerHealthy -Container 'maf-postgres')) {
    Stop-Run "Postgres did not become healthy in time" @("docker compose logs postgres")
}
Write-Ok "postgres healthy"

if (-not (Wait-ContainerHealthy -Container 'maf-redis')) {
    Stop-Run "Redis did not become healthy in time" @("docker compose logs redis")
}
Write-Ok "redis healthy"

if (-not (Wait-TcpPort -TargetHost '127.0.0.1' -TargetPort 6333)) {
    Stop-Run "Qdrant is not accepting connections on 6333" @("docker compose logs qdrant")
}
Write-Ok "qdrant ready"

# ---------------------------------------------------------------- 4-5. database

if ($SkipSeed) {
    Write-Step "Skipping schema + seed (-SkipSeed)"
} else {
    Write-Step "Creating the database schema"
    & $Python (Join-Path $Root 'scripts\init_db.py')
    if ($LASTEXITCODE -ne 0) { Stop-Run "scripts/init_db.py failed" }

    Write-Step "Seeding the shop"
    & $Python -m demo.shopping_assistant.seed
    if ($LASTEXITCODE -ne 0) { Stop-Run "demo.shopping_assistant.seed failed" }
}

# ---------------------------------------------------------------- 6-7. servers

$backend = $null
$frontend = $null

try {
    Write-Step ("Starting the API on :" + $Port)

    if (Test-TcpPort -TargetHost '127.0.0.1' -TargetPort $Port) {
        Stop-Run ("Port " + $Port + " is already in use") @(
            "Stop the process using it, or pick another:  .\run.ps1 -Port 8001"
        )
    }

    # serve.py, not a bare uvicorn: on Windows uvicorn picks ProactorEventLoop, which
    # async psycopg rejects, and the API would boot with persistence silently disabled.
    $backend = Start-Process -FilePath $Python `
        -ArgumentList 'scripts\serve.py', '--port', $Port `
        -WorkingDirectory $Root -NoNewWindow -PassThru

    # The lifespan opens Postgres before serving, and that first connection is slow on
    # some Windows setups (minutes, not seconds), so wait generously and show progress -
    # a silent wait is indistinguishable from a hang.
    $health = $null
    $started = Get-Date
    $deadline = $started.AddSeconds($ReadyTimeoutSec)
    $nextNote = 15
    while ((Get-Date) -lt $deadline) {
        if ($backend.HasExited) { Stop-Run "The API exited during startup - see the log above." }
        try {
            $health = Invoke-RestMethod -Uri ("http://127.0.0.1:{0}/health" -f $Port) -TimeoutSec 3
            break
        } catch {
            Start-Sleep -Seconds 1
        }
        $waited = [int]((Get-Date) - $started).TotalSeconds
        if ($waited -ge $nextNote) {
            Write-Note ("still waiting for /health ({0}s) - the first Postgres connection is the slow part" -f $waited)
            $nextNote += 15
        }
    }
    if ($null -eq $health) {
        Stop-Run ("The API never answered /health within " + $ReadyTimeoutSec + "s") @(
            "The server may still be starting. Give it longer with:",
            "  .\run.ps1 -ReadyTimeoutSec 600"
        )
    }
    Write-Note ("ready in {0}s" -f [int]((Get-Date) - $started).TotalSeconds)

    Write-Ok ("api " + $health.status)
    if ($health.components) {
        foreach ($name in $health.components.PSObject.Properties.Name) {
            $value = $health.components.$name
            $line = "    " + $name + ": " + $value
            if ($value -eq 'ok') {
                Write-Host $line -ForegroundColor Green
            } else {
                Write-Host $line -ForegroundColor Yellow
            }
        }
    }

    if (-not $NoFrontend) {
        Write-Step ("Starting the UI on :" + $FrontendPort)
        $frontend = Start-Process -FilePath $Npm -ArgumentList 'run', 'dev' `
            -WorkingDirectory (Join-Path $Root 'frontend') -NoNewWindow -PassThru

        if (Wait-TcpPort -TargetHost '127.0.0.1' -TargetPort $FrontendPort -TimeoutSec 60) {
            Write-Ok ("ui  http://localhost:" + $FrontendPort)
            if (-not $NoBrowser) {
                Start-Process ("http://localhost:{0}" -f $FrontendPort) | Out-Null
            }
        } else {
            Write-Warn "The Vite dev server did not come up - see the log above."
        }
    }

    Write-Host ""
    Write-Host "-------------------------------------------------------------" -ForegroundColor DarkGray
    Write-Host ("  API   http://localhost:{0}       docs: /docs" -f $Port) -ForegroundColor White
    if (-not $NoFrontend) {
        Write-Host ("  UI    http://localhost:{0}" -f $FrontendPort) -ForegroundColor White
        Write-Host "  Sign in with a demo account (the login page lists them)." -ForegroundColor DarkGray
    }
    Write-Host "  Ctrl+C stops both servers. Docker keeps running." -ForegroundColor DarkGray
    Write-Host "-------------------------------------------------------------" -ForegroundColor DarkGray
    Write-Host ""

    # Both children share this console, so their logs print here and Ctrl+C reaches them.
    while ($true) {
        if ($backend.HasExited) {
            Write-Warn ("The API exited (code " + $backend.ExitCode + ").")
            break
        }
        if ($null -ne $frontend -and $frontend.HasExited) {
            Write-Warn ("The UI exited (code " + $frontend.ExitCode + ").")
            break
        }
        Start-Sleep -Seconds 1
    }
} finally {
    Write-Host ""
    Write-Host "Shutting down..." -ForegroundColor Cyan
    Stop-Tree $frontend
    Stop-Tree $backend
    Write-Host "Stopped. Docker services are still up (docker compose down to stop them)." -ForegroundColor DarkGray
}

} finally {
    Pop-Location
}
