$ErrorActionPreference = "Stop"
Write-Host "=== Configurar Gemini Smart Vision - Cime dos Mundos 5.0 ==="
$secureKey = Read-Host "Cole a chave Gemini API (entrada oculta)" -AsSecureString
if ($secureKey.Length -eq 0) {
    Write-Error "A chave nao pode ficar vazia."
    exit 1
}
$ptr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secureKey)
try {
    $plainKey = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($ptr)
    $envPath = Join-Path $PSScriptRoot ".env"
    if (Test-Path $envPath) { $lines = @(Get-Content -Path $envPath) } else { $lines = @() }
    $updates = [ordered]@{
        "GEMINI_API_KEY" = $plainKey
        "GEMINI_VISION_MODEL" = "gemini-3.8-flash"
    }
    foreach ($keyName in $updates.Keys) {
        $pattern = "^\s*" + [regex]::Escape($keyName) + "\s*="
        $found = $false
        for ($i = 0; $i -lt $lines.Count; $i++) {
            if ($lines[$i] -match $pattern) {
                $lines[$i] = "$keyName=$($updates[$keyName])"
                $found = $true
            }
        }
        if (-not $found) { $lines += "$keyName=$($updates[$keyName])" }
    }
    [System.IO.File]::WriteAllLines($envPath, [string[]]$lines, [System.Text.UTF8Encoding]::new($false))
    Write-Host "A chave foi salva em .env (arquivo local)."
    Write-Host "Modelo visual: gemini-3.8-flash"
    Write-Host "Nao compartilhe .env nem envie a chave para o GitHub."
} finally {
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($ptr)
    $plainKey = $null
}
