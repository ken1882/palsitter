param(
    [string]$Output = "$(Join-Path $PSScriptRoot '..\git-runtime')"
)

$ErrorActionPreference = "Stop"
$gitCommand = Get-Command git.exe -ErrorAction Stop
$gitRoot = Split-Path (Split-Path $gitCommand.Source -Parent) -Parent
$outputPath = if ([IO.Path]::IsPathRooted($Output)) {
    [IO.Path]::GetFullPath($Output)
} else {
    [IO.Path]::GetFullPath((Join-Path $PSScriptRoot $Output))
}

if (Test-Path -LiteralPath $outputPath) {
    Remove-Item -LiteralPath $outputPath -Recurse -Force
}
New-Item -ItemType Directory -Path $outputPath -Force | Out-Null

foreach ($directory in @("cmd", "etc", "mingw64", "usr")) {
    $source = Join-Path $gitRoot $directory
    if (-not (Test-Path -LiteralPath $source)) {
        throw "Git runtime directory is missing: $source"
    }
    Copy-Item -LiteralPath $source -Destination (Join-Path $outputPath $directory) -Recurse -Force
}

& (Join-Path $outputPath 'cmd\git.exe') --version
if ($LASTEXITCODE -ne 0) {
    throw "Bundled Git runtime failed its version smoke test"
}
