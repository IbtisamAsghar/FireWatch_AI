$ErrorActionPreference = 'Stop'
$workspace = 'C:/Users/talha/Desktop/FireWatch_AI'
$runtimeDirectory = Join-Path $workspace '.local/readiness'
$pythonPath = Join-Path $workspace '.venv/Scripts/python.exe'
foreach ($service in @('api', 'worker')) {
    $recordPath = Join-Path $runtimeDirectory ($service + '-process.json')
    if (Test-Path -LiteralPath $recordPath) {
        $record = Get-Content -LiteralPath $recordPath -Raw | ConvertFrom-Json
        if ($record.root -ne [System.IO.Path]::GetFullPath($workspace)) { throw 'Unexpected service owner directory' }
        $existing = Get-CimInstance Win32_Process -Filter ('ProcessId = ' + [int]$record.pid)
        if ($null -ne $existing) {
            $moduleName = if ($service -eq 'api') { 'firewatch.main:app' } else { 'firewatch.worker' }
            if ($existing.CommandLine -notlike ('*' + $moduleName + '*')) { throw 'Recorded PID no longer belongs to the expected service' }
            if ($existing.CommandLine -notlike '*FireWatch_AI*') { throw 'Recorded service command is outside this workspace' }
            $children = Get-CimInstance Win32_Process -Filter ('ParentProcessId = ' + [int]$record.pid)
            foreach ($child in $children) {
                if ($child.Name -eq 'conhost.exe' -and $child.ExecutablePath -eq 'C:\WINDOWS\system32\conhost.exe') { continue }
                if ($child.CommandLine -notlike ('*' + $moduleName + '*')) { throw 'Unexpected child process; refusing restart' }
                Stop-Process -Id $child.ProcessId -ErrorAction SilentlyContinue
            }
            Stop-Process -Id $record.pid -ErrorAction SilentlyContinue
        }
    }
}
$stamp = Get-Date -Format 'yyyyMMddHHmmss'
$apiArguments = @('-m', 'uvicorn', 'firewatch.main:app', '--host', '127.0.0.1', '--port', '8000')
$api = Start-Process -FilePath $pythonPath -ArgumentList $apiArguments -WorkingDirectory $workspace -WindowStyle Hidden -RedirectStandardOutput (Join-Path $runtimeDirectory ('api-' + $stamp + '.stdout.log')) -RedirectStandardError (Join-Path $runtimeDirectory ('api-' + $stamp + '.stderr.log')) -PassThru
@{pid=$api.Id;port=8000;command='uvicorn firewatch.main:app';root=[System.IO.Path]::GetFullPath($workspace)} | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $runtimeDirectory 'api-process.json')
$worker = Start-Process -FilePath $pythonPath -ArgumentList @('-m', 'firewatch.worker', '--no-schedule') -WorkingDirectory $workspace -WindowStyle Hidden -RedirectStandardOutput (Join-Path $runtimeDirectory ('worker-' + $stamp + '.stdout.log')) -RedirectStandardError (Join-Path $runtimeDirectory ('worker-' + $stamp + '.stderr.log')) -PassThru
@{pid=$worker.Id;command='firewatch.worker --no-schedule';root=[System.IO.Path]::GetFullPath($workspace)} | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $runtimeDirectory 'worker-process.json')
Write-Output 'Local API and replay-only worker started with the current configuration.'
