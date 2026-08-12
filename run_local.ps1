<#
.SYNOPSIS
    Levanta la topologia completa en local, cada nodo en su propia ventana.

.DESCRIPTION
    Abre una ventana de PowerShell por router, una para el banco y una para el
    ATM. Respeta el orden de arranque del contrato: primero banco y routers,
    y el ATM al final, cuando las tablas de ruteo ya convergieron.

.PARAMETER Topologia
    'simple'  : U - V - X (tres nodos, la del contrato de interoperabilidad).
    'ejemplo' : A..I (nueve nodos, con rutas alternativas para demostrar Dijkstra).

.PARAMETER EsperaConvergencia
    Segundos de espera antes de abrir el ATM. Por omision 15.

.EXAMPLE
    .\run_local.ps1
    .\run_local.ps1 -Topologia ejemplo
#>
param(
    [ValidateSet('simple', 'ejemplo')]
    [string]$Topologia = 'simple',

    [int]$EsperaConvergencia = 15
)

$ErrorActionPreference = 'Stop'
$raiz = $PSScriptRoot

if ($Topologia -eq 'simple') {
    $configDir = Join-Path $raiz 'config'
    $routers = @('router_u.json', 'router_v.json', 'router_x.json')
} else {
    $configDir = Join-Path $raiz 'config\topologia_ejemplo'
    $routers = @(
        'router_a.json', 'router_b.json', 'router_c.json', 'router_d.json',
        'router_e.json', 'router_f.json', 'router_g.json', 'router_h.json',
        'router_i.json'
    )
}

function Start-Nodo {
    param([string]$Titulo, [string]$Script, [string]$Config)

    $comando = "`$Host.UI.RawUI.WindowTitle = '$Titulo'; " +
               "python -u '$(Join-Path $raiz "src\$Script")' '$Config'"
    Start-Process powershell -ArgumentList '-NoExit', '-Command', $comando | Out-Null
}

Write-Host "Topologia: $Topologia" -ForegroundColor Cyan

# 1. El banco primero: debe estar escuchando antes de que lleguen paquetes.
Start-Nodo -Titulo 'BANCO' -Script 'bank_server.py' `
           -Config (Join-Path $configDir 'host_bank.json')

# 2. Los routers: ejecutan HELLO, LSA, flooding y Dijkstra.
foreach ($config in $routers) {
    $id = ($config -replace 'router_', '' -replace '\.json', '').ToUpper()
    Write-Host "  levantando router $id"
    Start-Nodo -Titulo "ROUTER $id" -Script 'router.py' `
               -Config (Join-Path $configDir $config)
    Start-Sleep -Milliseconds 300
}

# 3. El ATM al final, con las tablas ya estables.
Write-Host "Esperando $EsperaConvergencia s a que converjan las tablas..." `
           -ForegroundColor Yellow
Start-Sleep -Seconds $EsperaConvergencia

Start-Nodo -Titulo 'ATM' -Script 'atm_client.py' `
           -Config (Join-Path $configDir 'host_atm.json')

Write-Host ''
Write-Host 'Nodos levantados. Las tablas estan en data\*.csv' -ForegroundColor Green
Write-Host 'Para detener todo: Get-Process python | Stop-Process -Force'
