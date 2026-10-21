param(
  [string]$Python = "python"
)

& $Python -m playwright install chromium
if ($LASTEXITCODE -ne 0) {
  throw "Chromium installation failed."
}
