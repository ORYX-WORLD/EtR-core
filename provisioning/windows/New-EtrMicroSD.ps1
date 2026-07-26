[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateScript({ Test-Path -LiteralPath $_ -PathType Leaf })]
    [string]$ImagePath,

    [Parameter(Mandatory = $true)]
    [ValidateRange(0, 999)]
    [int]$DiskNumber,

    [string]$ImagerPath = "${env:ProgramFiles(x86)}\Raspberry Pi Imager\rpi-imager.exe",

    [string]$SshPublicKeyPath = "$env:USERPROFILE\.ssh\id_ed25519.pub"
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

function Assert-Administrator {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = [Security.Principal.WindowsPrincipal]::new($identity)
    if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        throw "Relancez PowerShell en tant qu'administrateur."
    }
}

function Resolve-BootVolume {
    param([int]$TargetDiskNumber)

    for ($attempt = 0; $attempt -lt 60; $attempt++) {
        Update-HostStorageCache
        $partition = Get-Partition -DiskNumber $TargetDiskNumber -ErrorAction SilentlyContinue |
            Where-Object {
                $_.Size -lt 2GB -and
                $_.AccessPaths -match '^[A-Z]:\\$'
            } |
            Select-Object -First 1
        if ($partition) {
            return ($partition.AccessPaths | Where-Object { $_ -match '^[A-Z]:\\$' } | Select-Object -First 1)
        }
        Start-Sleep -Seconds 2
    }
    throw "La partition de démarrage n'a pas été remontée par Windows."
}

Assert-Administrator

if (-not (Test-Path -LiteralPath $ImagerPath -PathType Leaf)) {
    throw "Raspberry Pi Imager est introuvable : $ImagerPath"
}

$disk = Get-Disk -Number $DiskNumber
$systemDiskNumber = (Get-Partition -DriveLetter $env:SystemDrive.TrimEnd(':')).DiskNumber
if ($disk.Number -eq $systemDiskNumber -or $disk.IsBoot -or $disk.IsSystem) {
    throw "Refus de toucher au disque système."
}
if ($disk.Size -gt 256GB) {
    throw "Refus de sécurité : le disque dépasse 256 Go."
}
if ($disk.BusType -notin @("USB", "SD", "MMC")) {
    throw "Refus de sécurité : le support n'est pas identifié comme USB/SD/MMC."
}

$sizeGB = [math]::Round($disk.Size / 1GB, 1)
Write-Host ""
Write-Host "SUPPORT QUI SERA ENTIÈREMENT EFFACÉ" -ForegroundColor Red
Write-Host "Disque : $($disk.Number)"
Write-Host "Nom    : $($disk.FriendlyName)"
Write-Host "Bus    : $($disk.BusType)"
Write-Host "Taille : $sizeGB Go"
Write-Host "Image  : $ImagePath"
Write-Host ""

$confirmation = Read-Host "Tapez exactement EFFACER-$DiskNumber"
if ($confirmation -cne "EFFACER-$DiskNumber") {
    throw "Opération annulée : confirmation incorrecte."
}

if (-not (Test-Path -LiteralPath $SshPublicKeyPath -PathType Leaf)) {
    $privateKeyPath = [IO.Path]::ChangeExtension($SshPublicKeyPath, $null)
    New-Item -ItemType Directory -Force -Path (Split-Path $privateKeyPath) | Out-Null
    & ssh-keygen.exe -q -t ed25519 -N '""' -C "etr-factory@$env:COMPUTERNAME" -f $privateKeyPath
    if ($LASTEXITCODE -ne 0) {
        throw "La génération de la clé SSH a échoué."
    }
}

Write-Host "Écriture et vérification de l'image..." -ForegroundColor Cyan
$physicalDrive = "\\.\PhysicalDrive$DiskNumber"
& $ImagerPath --cli (Resolve-Path -LiteralPath $ImagePath).Path $physicalDrive
if ($LASTEXITCODE -ne 0) {
    throw "Raspberry Pi Imager a échoué avec le code $LASTEXITCODE."
}

$bootRoot = Resolve-BootVolume -TargetDiskNumber $DiskNumber
$repoRoot = Resolve-Path (Join-Path $PSScriptRoot "..\..")
$bootstrapSource = Join-Path $repoRoot "provisioning\raspi\etr-firstboot.sh"
Copy-Item -LiteralPath $bootstrapSource -Destination (Join-Path $bootRoot "etr-firstboot.sh") -Force
Copy-Item -LiteralPath $SshPublicKeyPath -Destination (Join-Path $bootRoot "etr-authorized-key.pub") -Force
New-Item -ItemType File -Path (Join-Path $bootRoot "ssh") -Force | Out-Null

$cmdlinePath = Join-Path $bootRoot "cmdline.txt"
$cmdline = (Get-Content -LiteralPath $cmdlinePath -Raw).Trim()
$firstBootArgs = "systemd.run=/boot/firmware/etr-firstboot.sh systemd.run_success_action=reboot systemd.unit=kernel-command-line.target"
if ($cmdline -notmatch [regex]::Escape("systemd.run=/boot/firmware/etr-firstboot.sh")) {
    Set-Content -LiteralPath $cmdlinePath -Value "$cmdline $firstBootArgs" -NoNewline -Encoding ascii
}

Write-Host ""
Write-Host "Carte EtR prête." -ForegroundColor Green
Write-Host "1. Éjectez proprement le support."
Write-Host "2. Insérez-le dans le Raspberry Pi."
Write-Host "3. Branchez Ethernet avant la première mise sous tension."
Write-Host "4. L'identité sera créée automatiquement : etr-<8 derniers caractères du numéro matériel>."

