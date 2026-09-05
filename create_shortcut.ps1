# 创建桌面快捷方式 (Windows PowerShell)
$exePath = Join-Path $PSScriptRoot "dist\PPT2Obsidian\PPT2Obsidian.exe"
if (-not (Test-Path $exePath)) {
    Write-Host "错误: 未找到 $exePath" -ForegroundColor Red
    Write-Host "请先运行 build_exe.py 打包"
    exit 1
}
$desktop = [Environment]::GetFolderPath("Desktop")
$WshShell = New-Object -ComObject WScript.Shell
$shortcut = $WshShell.CreateShortcut("$desktop\PPT2Obsidian.lnk")
$shortcut.TargetPath = $exePath
$shortcut.WorkingDirectory = (Split-Path $exePath)
$shortcut.IconLocation = $exePath
$shortcut.Save()
Write-Host "桌面快捷方式已创建: $desktop\PPT2Obsidian.lnk" -ForegroundColor Green
