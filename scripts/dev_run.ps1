# 개발용 실행 (Windows): 저장소 소스를 설치된 3DPainter 확장 폴더에 동기화하고 Blender 를 띄운다.
#
# 사용법 (PowerShell):
#   .\scripts\dev_run.ps1              # 동기화 후 Blender 실행 (이미 실행 중이면 동기화만)
#   .\scripts\dev_run.ps1 -NoLaunch    # 동기화만
#   $env:BLENDER = "C:\...\blender.exe"; .\scripts\dev_run.ps1
# 실행 정책에 막히면 scripts\dev_run.bat 을 쓴다.
#
# 전제: 확장이 zip 으로 한 번은 설치돼 있어야 한다 (휠 설치·활성화는 Blender 가 담당).

param([switch]$NoLaunch)

$ErrorActionPreference = 'Stop'
$ExtId = 'painter3d'
$RepoRoot = Split-Path -Parent $PSScriptRoot

# BLENDER 미지정 시 Program Files 에 설치된 버전 중 가장 높은 것을 쓴다
$Blender = $env:BLENDER
if (-not $Blender) {
    $Blender = Get-ChildItem "$env:ProgramFiles\Blender Foundation\Blender *\blender.exe" -ErrorAction SilentlyContinue |
        Sort-Object { [version]($_.Directory.Name -replace '^Blender\s+', '') } -Descending |
        Select-Object -First 1 -ExpandProperty FullName
}
if (-not $Blender -or -not (Test-Path $Blender)) {
    Write-Error "Blender 실행 파일을 찾을 수 없습니다. `$env:BLENDER 로 경로를 지정하세요."
}

# "Blender 5.2.0 LTS" → 5.2
$VersionLine = & $Blender --version 2>$null | Select-String '^Blender (\d+)\.(\d+)' | Select-Object -First 1
if (-not $VersionLine) { Write-Error "Blender 버전을 읽을 수 없습니다: $Blender" }
$Version = "$($VersionLine.Matches[0].Groups[1].Value).$($VersionLine.Matches[0].Groups[2].Value)"
$ExtRoot = Join-Path $env:APPDATA "Blender Foundation\Blender\$Version\extensions"

# 어느 저장소(repo)에 설치돼 있든 찾아서 그 자리에 덮어쓴다 (.local 은 휠 전용이라 제외)
$Target = Get-ChildItem $ExtRoot -Directory -ErrorAction SilentlyContinue |
    Where-Object { $_.Name -ne '.local' } |
    ForEach-Object { Join-Path $_.FullName $ExtId } |
    Where-Object { Test-Path (Join-Path $_ 'blender_manifest.toml') } |
    Select-Object -First 1
if (-not $Target) {
    Write-Error "Blender $Version 에 $ExtId 확장이 설치돼 있지 않습니다. 먼저 build.sh 로 만든 zip 을 Install from Disk 로 한 번 설치하세요."
}

# 제외 목록은 blender_manifest.toml 의 [build] paths_exclude_pattern 과 맞춘다
$ExcludeDirs = @('.git', '.github', '.serena', '.ruff_cache', '.omc', '.venv', '__pycache__',
    (Join-Path $RepoRoot 'scripts'), (Join-Path $RepoRoot 'dist'),
    (Join-Path $RepoRoot 'operators\brushes\art\src'))
$ExcludeFiles = @('.gitignore', '.prettierrc', '.python-version', 'pyproject.toml', 'uv.lock',
    'run_tests.py', 'donation_cache.json', 'version_cache.json',
    '*.pyc', '*.pyo', '*.pyd', '.DS_Store', '*.blend1', '*.zip')

robocopy $RepoRoot $Target /MIR /NFL /NDL /NJH /NJS /NP /XD @ExcludeDirs /XF @ExcludeFiles | Out-Null
# robocopy 는 8 미만이 성공
if ($LASTEXITCODE -ge 8) { Write-Error "robocopy 실패 (코드 $LASTEXITCODE)" }
$global:LASTEXITCODE = 0

Write-Host "동기화 완료 → $Target"

if (Get-Process blender -ErrorAction SilentlyContinue) {
    Write-Host "Blender 실행 중 — F3 > Reload Scripts 또는 재시작해야 반영됩니다."
} elseif (-not $NoLaunch) {
    Start-Process $Blender
    Write-Host "Blender $Version 실행"
}
