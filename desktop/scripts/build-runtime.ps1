param(
    [string]$PythonVersion = "3.12.10",
    [string]$Output = "$(Join-Path $PSScriptRoot '..\runtime')"
)

$ErrorActionPreference = "Stop"
$expectedPythonVersion = [Version]$PythonVersion
$expectedPythonAbi = "$($expectedPythonVersion.Major).$($expectedPythonVersion.Minor)"
$buildPythonInfo = (& python -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}|{sys.executable}')").Trim()
if ($LASTEXITCODE -ne 0) {
    throw "Could not inspect the Python executable used to install runtime dependencies"
}
$buildPythonAbi, $buildPythonExecutable = $buildPythonInfo -split '\|', 2
if ($buildPythonAbi -ne $expectedPythonAbi) {
    throw "Python $expectedPythonAbi is required to build embedded Python $PythonVersion, but 'python' is $buildPythonAbi at $buildPythonExecutable"
}

$outputPath = if ([IO.Path]::IsPathRooted($Output)) {
    [IO.Path]::GetFullPath($Output)
} else {
    [IO.Path]::GetFullPath((Join-Path $PSScriptRoot $Output))
}
if (Test-Path -LiteralPath $outputPath) {
    Remove-Item -LiteralPath $outputPath -Recurse -Force
}
New-Item -ItemType Directory -Path $outputPath -Force | Out-Null

$archive = Join-Path $env:TEMP "palsitter-python-$PythonVersion-embed-amd64.zip"
$url = "https://www.python.org/ftp/python/$PythonVersion/python-$PythonVersion-embed-amd64.zip"
Invoke-WebRequest -Uri $url -OutFile $archive
Expand-Archive -LiteralPath $archive -DestinationPath $outputPath -Force
New-Item -ItemType Directory -Path (Join-Path $outputPath 'Lib\site-packages') -Force | Out-Null

$pth = Get-ChildItem -LiteralPath $outputPath -Filter '*._pth' | Select-Object -First 1
if (-not $pth) {
    throw "The embedded Python archive did not contain a _pth file"
}
Set-Content -LiteralPath $pth.FullName -Value @(
    "python312.zip",
    ".",
    "../backend",
    "Lib/site-packages",
    "import site"
) -Encoding ascii

python -m pip install --disable-pip-version-check --target (Join-Path $outputPath 'Lib\site-packages') -r (Join-Path $PSScriptRoot '..\..\requirements-runtime.txt')
if ($LASTEXITCODE -ne 0) {
    throw "Could not install the bundled Python runtime dependencies"
}
& (Join-Path $outputPath 'python.exe') -c "import psutil, pywebio, requests, winpty; print('Palsitter runtime ready')"
if ($LASTEXITCODE -ne 0) {
    throw "The bundled Python runtime failed its import smoke test"
}
