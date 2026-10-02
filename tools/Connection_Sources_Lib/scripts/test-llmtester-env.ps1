param(
    [switch]$Force
)

$KnownHostsPath = Join-Path $HOME "gitlab_dom_tti_known_hosts"
if ($Force -or -not (Test-Path $KnownHostsPath)) {
    $lines = ssh-keygen -F gitlab.dom.tti -f "$HOME\.ssh\known_hosts" | Where-Object { $_ -notmatch '^#' }
    if (-not $lines) {
        throw "gitlab.dom.tti not found in `$HOME\.ssh\known_hosts -- connect to it once with a normal git/ssh command first so its host key is trusted, then re-run this script"
    }
    $lines | Set-Content $KnownHostsPath
}

$env:VAULT_ADDR = "http://127.0.0.1:8200"
$env:VAULT_TOKEN = "talan-dev-root-token"
$env:GITLAB_SSH_KNOWN_HOSTS = $KnownHostsPath
$Test = Join-Path $HOME "gitlab-test\LLMTester"

Write-Host "VAULT_ADDR = $env:VAULT_ADDR"
Write-Host "GITLAB_SSH_KNOWN_HOSTS = $env:GITLAB_SSH_KNOWN_HOSTS"
Write-Host "`$Test = $Test"
