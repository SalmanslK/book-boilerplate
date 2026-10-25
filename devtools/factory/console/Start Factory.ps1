# Starts By the Book, the AI factory's app, and opens it in your browser.
# Right-click > Run with PowerShell, or run:  powershell -ExecutionPolicy Bypass -File "Start Factory.ps1"
# Leave the window open while you use the app; close it to stop the app.
$Host.UI.RawUI.WindowTitle = "By the Book - leave this window open"
Set-Location -LiteralPath $PSScriptRoot
if (Get-Command py -ErrorAction SilentlyContinue) { py -3 console.py --open @args } else { python console.py --open @args }
if ($LASTEXITCODE -ne 0) {
    Write-Host ""
    Write-Host "The app could not start. Is Python 3.11 or newer installed?"
    Read-Host "Press Enter to close"
}
