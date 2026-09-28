param(
    [ValidateSet('zh', 'en', 'both')][string]$Language = 'zh',
    [string]$OutputDirectory = 'dist'
)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$pythonPath = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $pythonPath)) { throw 'Create .venv and install requirements-lock.txt first.' }
$editions = if ($Language -eq 'both') { @('zh', 'en') } else { @($Language) }
$outputRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot $OutputDirectory))
if (-not $outputRoot.StartsWith($PSScriptRoot + [System.IO.Path]::DirectorySeparatorChar, [System.StringComparison]::OrdinalIgnoreCase)) { throw 'Output directory must be inside the project.' }
foreach ($edition in $editions) {
    & $pythonPath 'tools\localize.py' $edition
    if ($LASTEXITCODE -ne 0) { throw 'Source preparation failed.' }
    $sourceRoot = Join-Path $PSScriptRoot ('build\source-' + $edition)
    $workRoot = Join-Path $PSScriptRoot ('build\package-' + $edition)
    & $pythonPath -m PyInstaller --noconfirm --onefile --windowed --name ('CSE-Space-Helper-' + $edition) --distpath $outputRoot --workpath $workRoot --specpath $sourceRoot --icon (Join-Path $sourceRoot 'app.ico') --add-data ((Join-Path $sourceRoot 'remote_engine.py') + ';.') --add-data ((Join-Path $sourceRoot 'app.ico') + ';.') (Join-Path $sourceRoot 'app.py')
    if ($LASTEXITCODE -ne 0) { throw ('Packaging failed: ' + $edition) }
    Write-Output (Join-Path $outputRoot ('CSE-Space-Helper-' + $edition + '.exe'))
}
