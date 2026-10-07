param([switch]$UpdateOnly, [string]$Command)
$ErrorActionPreference = 'Stop'
$profileName = 'ktc-notebook'
$regionName = 'ap-northeast-2'
$instanceId = 'i-0ddf82de2ba56da70'
$groupId = 'sg-0b0dfc0677cdd60eb'

function Invoke-IsolationAws {
    param([string[]]$Arguments)
    $result = & aws @Arguments --profile $profileName --region $regionName --output json --no-cli-pager
    if ($LASTEXITCODE -ne 0) { throw 'AWS command failed. If your SSO session expired, run: aws sso login --profile ktc-notebook' }
    return ($result -join "`n" | ConvertFrom-Json)
}

# A Wi-Fi change can change the public NAT address. SSH still requires this notebook's private key.
$currentIp = (Invoke-RestMethod -Uri 'https://checkip.amazonaws.com' -TimeoutSec 10).Trim()
$parsedIp = [Net.IPAddress]::Parse($currentIp)
if ($parsedIp.AddressFamily -ne [Net.Sockets.AddressFamily]::InterNetwork) { throw 'An IPv4 address is required.' }
$rules = Invoke-IsolationAws -Arguments @('ec2', 'describe-security-group-rules', '--filters', "Name=group-id,Values=$groupId")
$managedRules = @($rules.SecurityGroupRules | Where-Object {
    -not $_.IsEgress -and $_.Description -eq 'ktc-isolation-notebook' -and
    $_.IpProtocol -eq 'tcp' -and $_.FromPort -eq 22 -and $_.ToPort -eq 22
})
if ($managedRules.Count -ne 1) { throw 'Expected exactly one notebook SSH rule; refusing to change other rules.' }
$rule = $managedRules[0]
if ($rule.CidrIpv4 -ne "$currentIp/32") {
    $requestPath = [IO.Path]::GetTempFileName()
    try {
        $request = @(@{
            SecurityGroupRuleId = $rule.SecurityGroupRuleId
            SecurityGroupRule = @{
                IpProtocol = 'tcp'; FromPort = 22; ToPort = 22
                CidrIpv4 = "$currentIp/32"; Description = 'ktc-isolation-notebook'
            }
        })
        [IO.File]::WriteAllText($requestPath, (ConvertTo-Json -InputObject $request -Depth 5), [Text.UTF8Encoding]::new($false))
        Invoke-IsolationAws -Arguments @('ec2', 'modify-security-group-rules', '--group-id', $groupId, '--security-group-rules', "file://$requestPath") | Out-Null
    } finally { Remove-Item -LiteralPath $requestPath -Force }
}
$instances = Invoke-IsolationAws -Arguments @('ec2', 'describe-instances', '--instance-ids', $instanceId)
$serverIp = $instances.Reservations[0].Instances[0].PublicIpAddress
if (-not $serverIp) { throw 'The instance has no public IPv4 address.' }
Write-Host "SSH allowed from $currentIp/32; server $serverIp."
if ($UpdateOnly) { return }
$sshArguments = @(
    '-o', 'ProxyCommand=none', '-o', "HostKeyAlias=$instanceId",
    '-o', 'StrictHostKeyChecking=yes', '-o', 'IdentitiesOnly=yes',
    '-o', 'ForwardAgent=no', '-o', 'ConnectTimeout=15',
    '-i', "$env:USERPROFILE/.ssh/ktc_isolation_ed25519", "isolation-admin@$serverIp"
)
if ($Command) { $sshArguments += $Command }
& ssh @sshArguments
if ($LASTEXITCODE -ne 0) { throw "SSH exited with code $LASTEXITCODE" }
