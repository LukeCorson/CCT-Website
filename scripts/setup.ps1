$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $taskRoot
$taskPython = Join-Path $taskRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $taskPython)) {
    if (Get-Command py.exe -ErrorAction SilentlyContinue) {
        & py.exe -3 -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)"
        if ($LASTEXITCODE -ne 0) { throw 'Python 3.11 or newer is required. Update Python from python.org and retry.' }
        & py.exe -3 -m venv (Join-Path $taskRoot '.venv')
    } elseif (Get-Command python.exe -ErrorAction SilentlyContinue) {
        & python.exe -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)"
        if ($LASTEXITCODE -ne 0) { throw 'Python 3.11 or newer is required. Update Python from python.org and retry.' }
        & python.exe -m venv (Join-Path $taskRoot '.venv')
    } else {
        throw 'Install Python 3.11 or newer from python.org, then run setup again.'
    }
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $taskPython)) {
        throw 'Python could not create the environment. Install Python 3.11 or newer from python.org and retry.'
    }
}
& $taskPython -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)"
if ($LASTEXITCODE -ne 0) { throw 'The local environment needs Python 3.11 or newer. Recreate .venv with a supported Python installation.' }
& $taskPython -m pip install -r (Join-Path $taskRoot 'requirements.txt')
if ($LASTEXITCODE -ne 0) { throw 'Dependencies could not be installed. Check the internet connection and retry setup.' }
& $taskPython -c "import PySide6, pyqtgraph, serial, numpy; print('CCT desktop dependencies are ready.')"
if ($LASTEXITCODE -ne 0) { throw 'Dependency verification failed.' }
