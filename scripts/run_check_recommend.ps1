# run_check_recommend.ps1
# ---------------------------------------------------------------------------
# Convenience runner for scripts/check_recommend.py — the offline policy check
# for the recommend_intervention Cloud Function (drift guard + demo-cast policy
# trace + option-b/anchor edge cases). No network, no MongoDB.
#
# Resolves the repo .venv interpreter (falls back to PATH python) so the
# numpy/pandas import in generate_synthetic_data works, then runs the check and
# exits with its status code (0 = PASS, 1 = FAIL).
#
# Written 2026-06-06 (Day 7, Stint 18) so the check is one readable command to
# re-run before the demo. Expected duration: ~3s.
# ---------------------------------------------------------------------------

$ErrorActionPreference = "Stop"

# scripts/ is one level below the repo root.
$repoRoot = Split-Path -Parent $PSScriptRoot
$venvPy   = Join-Path $repoRoot ".venv\Scripts\python.exe"
$py       = if (Test-Path $venvPy) { $venvPy } else { "python" }
$check    = Join-Path $PSScriptRoot "check_recommend.py"

Write-Output "python : $py"
Write-Output "check  : $check"
Write-Output ""

& $py $check
exit $LASTEXITCODE
