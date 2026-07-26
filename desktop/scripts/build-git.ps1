param(
    [string]$Output = "$(Join-Path $PSScriptRoot '..\git-runtime')"
)

$ErrorActionPreference = "Stop"
$minGitUrl = "https://github.com/git-for-windows/git/releases/download/v2.55.0.windows.3/MinGit-2.55.0.3-64-bit.zip"
$minGitSha256 = "f48e2d2dc74a24454adc6d8fd0ac25bf9c2386f19cfb06202b9465aaad4f9f05"
$outputPath = if ([IO.Path]::IsPathRooted($Output)) {
    [IO.Path]::GetFullPath($Output)
} else {
    [IO.Path]::GetFullPath((Join-Path $PSScriptRoot $Output))
}
$archivePath = Join-Path ([IO.Path]::GetTempPath()) "palsitter-mingit-2.55.0.3.zip"

try {
    Invoke-WebRequest -Uri $minGitUrl -OutFile $archivePath
    $actualSha256 = (Get-FileHash -LiteralPath $archivePath -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($actualSha256 -ne $minGitSha256) {
        throw "MinGit checksum mismatch: expected $minGitSha256, got $actualSha256"
    }

    if (Test-Path -LiteralPath $outputPath) {
        Remove-Item -LiteralPath $outputPath -Recurse -Force
    }
    New-Item -ItemType Directory -Path $outputPath -Force | Out-Null
    Expand-Archive -LiteralPath $archivePath -DestinationPath $outputPath -Force
} finally {
    Remove-Item -LiteralPath $archivePath -Force -ErrorAction SilentlyContinue
}

& (Join-Path $outputPath 'cmd\git.exe') --version
if ($LASTEXITCODE -ne 0) {
    throw "Bundled Git runtime failed its version smoke test"
}
