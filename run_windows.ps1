[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
Write-Host "Starting REA Accounting Cloud..." -ForegroundColor Green

# 后台独立拉起后端进程
Start-Process -FilePath "dist\rea_backend.exe"

# 等待 2 秒
Start-Sleep -Seconds 2

# 使用 Edge 浏览器显式打开（若使用 Chrome 请替换对应路径）
Start-Process "msedge.exe" "http://127.0.0.1:8000"

Write-Host "System started. Please do not close this window." -ForegroundColor Yellow