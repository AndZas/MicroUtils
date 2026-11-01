param([switch]$Public, [string]$OutputDirectory)
$ErrorActionPreference = 'Stop'
$originalBuildPath = $env:PATH
Push-Location $PSScriptRoot
try {
    # Qt uses the OS ICU facade. Unrelated tools (e.g. Poppler) can put an
    # incompatible icuuc.dll on PATH; never bundle dependencies from that PATH.
    $pythonBase = & .\.venv\Scripts\python.exe -c 'import sys; print(sys.base_prefix)'
    if ($LASTEXITCODE -ne 0) { throw 'Cannot determine Python installation' }
    $env:PATH = ((Join-Path $PSScriptRoot '.venv\Scripts'), $pythonBase,
        (Join-Path $env:SystemRoot 'System32'), $env:SystemRoot) -join ';'
    $outputDir = if ($OutputDirectory) { $OutputDirectory } elseif ($Public) { 'dist-public' } else { 'dist' }
    & .\.venv\Scripts\python.exe app_icons.py
    if ($LASTEXITCODE -ne 0) { throw 'Cannot generate application icon' }
    & .\.venv\Scripts\python.exe generate_sounds.py
    if ($LASTEXITCODE -ne 0) { throw 'Cannot generate application sounds' }
    & .\.venv\Scripts\python.exe tools\collect_licenses.py
    if ($LASTEXITCODE -ne 0) { throw 'Cannot collect dependency licenses' }
    & .\.venv\Scripts\python.exe tools\windows_version.py
    if ($LASTEXITCODE -ne 0) { throw 'Cannot write Windows version information' }
    & .\.venv\Scripts\python.exe -m PyInstaller --noconfirm --clean --distpath $outputDir SoundControl.spec
    if ($LASTEXITCODE -ne 0) { throw 'PyInstaller failed' }
} finally {
    $env:PATH = $originalBuildPath
    Pop-Location
}
