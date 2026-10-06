$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
$projectRoot = (Resolve-Path .).Path
Push-Location frontend
try { npm run build; if ($LASTEXITCODE -ne 0) { throw 'Frontend build failed' } } finally { Pop-Location }
python -m PyInstaller --noconfirm --clean --onefile --windowed --name CampusRadar --icon "$projectRoot\campusradar.ico" --distpath release --workpath .build --specpath .build --paths "$projectRoot\backend" --add-data "$projectRoot\frontend\dist;frontend/dist" --add-data "$projectRoot\campusradar.ico;." --collect-all langgraph --collect-all webview --collect-all keyring desktop.py
if ($LASTEXITCODE -ne 0) { throw 'EXE build failed' }
New-Item -ItemType Directory -Path "$projectRoot\dist" -Force | Out-Null
Copy-Item -LiteralPath "$projectRoot\release\CampusRadar.exe" -Destination "$projectRoot\dist\CampusRadar.exe" -Force
