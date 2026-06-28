# Wrapper around run.py. Passes all args through, e.g. .\run.ps1 --dev
Set-Location -LiteralPath $PSScriptRoot
python run.py @args
exit $LASTEXITCODE
