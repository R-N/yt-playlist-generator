# Wrapper around install.py. Passes args through, e.g. .\install.ps1 --backend
Set-Location -LiteralPath $PSScriptRoot
python install.py @args
exit $LASTEXITCODE
