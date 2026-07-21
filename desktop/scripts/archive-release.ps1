param(
    [string]$InputDirectory = "$(Join-Path $PSScriptRoot '..\dist\win-unpacked')",
    [string]$OutputArchive = "$(Join-Path $PSScriptRoot '..\dist\Palsitter-win-x64.7z')"
)

$ErrorActionPreference = "Stop"
$inputPath = (Resolve-Path $InputDirectory).Path
$sevenZip = (Get-Command 7z -ErrorAction SilentlyContinue).Source
if (-not $sevenZip -and $env:ProgramFiles) {
    $candidate = Join-Path $env:ProgramFiles '7-Zip\7z.exe'
    if (Test-Path -LiteralPath $candidate) {
        $sevenZip = $candidate
    }
}
if (-not $sevenZip) {
    throw "7z is required to create the portable release archive"
}
if (Test-Path -LiteralPath $OutputArchive) {
    Remove-Item -LiteralPath $OutputArchive -Force
}
& $sevenZip a -t7z $OutputArchive (Join-Path $inputPath '*') | Out-Host
if ($LASTEXITCODE -ne 0) {
    throw "7z failed with exit code $LASTEXITCODE"
}
Get-FileHash -LiteralPath $OutputArchive -Algorithm SHA256 |
    ForEach-Object { "$($_.Hash)  $([IO.Path]::GetFileName($OutputArchive))" } |
    Set-Content -LiteralPath "$OutputArchive.sha256" -Encoding ascii
