param(
    [Parameter(ValueFromRemainingArguments=$true)]
    [string[]]$ProjectArguments
)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$projectPython = if ($env:CONDA_PREFIX -and (Test-Path -LiteralPath (Join-Path $env:CONDA_PREFIX 'python.exe'))) {
    Join-Path $env:CONDA_PREFIX 'python.exe'
} elseif (Get-Command conda -ErrorAction SilentlyContinue) {
    $projectCondaBase = (& conda info --base).Trim()
    $projectCondaPython = Join-Path $projectCondaBase 'python.exe'
    if (Test-Path -LiteralPath $projectCondaPython) { $projectCondaPython } else { (Get-Command python -ErrorAction Stop).Source }
} else {
    (Get-Command python -ErrorAction Stop).Source
}
& $projectPython -X utf8 -m reviewlab @ProjectArguments
exit $LASTEXITCODE
