# gen-secret.ps1 — reads .env and writes k8s/secret.yaml with real values
# Run from repo root:  .\k8s\scripts\gen-secret.ps1
# DO NOT commit the resulting secret.yaml to git.

$envPath = Join-Path $PSScriptRoot "../../.env"
$kv = @{}
Get-Content $envPath | Where-Object { $_ -match '^\s*[^#\s]' } | ForEach-Object {
    $parts = $_ -split '=', 2
    $kv[$parts[0].Trim().TrimStart([char]0xFEFF)] = $parts[1].Trim()
}

$yaml = @"
apiVersion: v1
kind: Secret
metadata:
  name: issuebell-secret
  namespace: issuebell
type: Opaque
stringData:
  SECRET_KEY: "$($kv['SECRET_KEY'])"
  DISCORD_BOT_TOKEN: "$($kv['DISCORD_BOT_TOKEN'])"
  DISCORD_CLIENT_ID: "$($kv['DISCORD_CLIENT_ID'])"
  DISCORD_CLIENT_SECRET: "$($kv['DISCORD_CLIENT_SECRET'])"
  GITHUB_CLIENT_ID: "$($kv['GITHUB_CLIENT_ID'])"
  GITHUB_CLIENT_SECRET: "$($kv['GITHUB_CLIENT_SECRET'])"
"@

$outPath = Join-Path $PSScriptRoot "../secret.yaml"
[System.IO.File]::WriteAllText($outPath, $yaml, [System.Text.UTF8Encoding]::new($false))
Write-Host "Written to $outPath — apply with: kubectl apply -f k8s/secret.yaml"
