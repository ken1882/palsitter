param(
    [string]$Output = "$(Join-Path $PSScriptRoot '..\source')"
)

$ErrorActionPreference = "Stop"
$metadataPath = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\git-metadata'))
$outputPath = if ([IO.Path]::IsPathRooted($Output)) {
    [IO.Path]::GetFullPath($Output)
} else {
    [IO.Path]::GetFullPath((Join-Path $PSScriptRoot $Output))
}
$repositoryRoot = (& git -c safe.directory=* rev-parse --show-toplevel).Trim()
$archive = Join-Path $env:TEMP "palsitter-source-$([guid]::NewGuid().ToString('N')).tar"

if (Test-Path -LiteralPath $outputPath) {
    Remove-Item -LiteralPath $outputPath -Recurse -Force
}
if (Test-Path -LiteralPath $metadataPath) {
    Remove-Item -LiteralPath $metadataPath -Recurse -Force
}
New-Item -ItemType Directory -Path $outputPath -Force | Out-Null

& git -c safe.directory=* -C $repositoryRoot archive --format=tar --output=$archive HEAD
if ($LASTEXITCODE -ne 0) {
    throw "Could not create a source archive from Git"
}
try {
    tar -xf $archive -C $outputPath
    if ($LASTEXITCODE -ne 0) {
        throw "Could not extract the source archive"
    }
} finally {
    Remove-Item -LiteralPath $archive -Force -ErrorAction SilentlyContinue
}

Copy-Item -LiteralPath (Join-Path $repositoryRoot '.git') -Destination $metadataPath -Recurse -Force
& git -c safe.directory=* --git-dir $metadataPath remote set-url origin "https://github.com/ken1882/palsitter.git"
if ($LASTEXITCODE -ne 0) {
    throw "Could not set the packaged updater remote"
}
& git -c safe.directory=* --git-dir $metadataPath config core.autocrlf false
if ($LASTEXITCODE -ne 0) {
    throw "Could not configure packaged Git metadata"
}

function Remove-PackagedGitConfigValue {
    param([string]$Key)

    & git -c safe.directory=* --git-dir $metadataPath config --local --unset-all $Key 2>$null
    if ($LASTEXITCODE -ne 0 -and $LASTEXITCODE -ne 5) {
        throw "Could not remove credential-related Git config: $Key"
    }
}

foreach ($key in @(
    "credential.helper",
    "http.extraheader",
    "http.https://github.com/.extraheader"
)) {
    Remove-PackagedGitConfigValue $key
}

$remoteNames = @(& git -c safe.directory=* --git-dir $metadataPath remote)
if ($LASTEXITCODE -ne 0) {
    throw "Could not inspect packaged Git remotes"
}
foreach ($remoteName in $remoteNames) {
    $remoteUrls = @(& git -c safe.directory=* --git-dir $metadataPath remote get-url --all $remoteName)
    if ($LASTEXITCODE -ne 0) {
        throw "Could not inspect packaged Git remote: $remoteName"
    }
    foreach ($remoteUrl in $remoteUrls) {
        if ($remoteUrl -match '^[a-z][a-z0-9+.-]*://[^/\s@]*@') {
            throw "Packaged Git remote contains embedded credentials: $remoteName"
        }
    }
}

& git -c safe.directory=* --git-dir $metadataPath config --local --get-regexp '(^credential\.helper$|^http\..*\.extraheader$)' 2>$null
if ($LASTEXITCODE -eq 0) {
    throw "Packaged Git metadata still contains credential-related config"
}
$metadataConfig = Get-Content -LiteralPath (Join-Path $metadataPath 'config') -Raw
if ($metadataConfig -match '(?i)authorization|bearer|extraheader|credential\.helper|token|[a-z][a-z0-9+.-]*://[^/\s@]*@') {
    throw "Packaged Git metadata contains credential material"
}
& git -c safe.directory=* -c core.autocrlf=true --git-dir $metadataPath --work-tree $outputPath add --all
if ($LASTEXITCODE -ne 0) {
    throw "Could not refresh packaged Git metadata"
}
$status = & git -c safe.directory=* --git-dir $metadataPath --work-tree $outputPath status --porcelain --untracked-files=all
if ($status) {
    throw "Packaged source is not clean: $($status -join '; ')"
}

if (-not (Test-Path -LiteralPath (Join-Path $outputPath 'gui.py'))) {
    throw "The staged source is missing gui.py"
}
