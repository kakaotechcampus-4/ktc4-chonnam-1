param(
    [switch]$UpdateOnly,
    [string]$Command,
    # Each device owns one SSH rule, so devices do not overwrite each other's address.
    [ValidatePattern('^[a-z0-9-]{1,32}$')]
    [string]$Device = $(if ($env:KTC_ISOLATION_DEVICE) { $env:KTC_ISOLATION_DEVICE } else { 'notebook' })
)
$ErrorActionPreference = 'Stop'
$profileName = 'ktc-notebook'
$regionName = 'ap-northeast-2'
$instanceId = 'i-0ddf82de2ba56da70'
$groupId = 'sg-0b0dfc0677cdd60eb'
$ruleDescription = "ktc-isolation-$Device"

function Invoke-IsolationAws {
    param([string[]]$Arguments)
    $result = & aws @Arguments --profile $profileName --region $regionName --output json --no-cli-pager
    if ($LASTEXITCODE -ne 0) { throw "AWS command failed. If your SSO session expired, run: aws sso login --profile $profileName" }
    return ($result -join "`n" | ConvertFrom-Json)
}

function Invoke-IsolationAwsWithFile {
    param([string[]]$Arguments, [string]$Option, $Body)
    $requestPath = [IO.Path]::GetTempFileName()
    try {
        [IO.File]::WriteAllText($requestPath, (ConvertTo-Json -InputObject $Body -Depth 5), [Text.UTF8Encoding]::new($false))
        Invoke-IsolationAws -Arguments ($Arguments + @($Option, "file://$requestPath")) | Out-Null
    } finally { Remove-Item -LiteralPath $requestPath -Force }
}

function Test-SshPort {
    param([string]$HostName)
    $client = [Net.Sockets.TcpClient]::new()
    try { return $client.ConnectAsync($HostName, 22).Wait(5000) -and $client.Connected }
    catch { return $false }
    finally { $client.Dispose() }
}

# A Wi-Fi change can change the public NAT address. SSH still requires this device's private key.
$currentIp = (Invoke-RestMethod -Uri 'https://checkip.amazonaws.com' -TimeoutSec 10).Trim()
$parsedIp = [Net.IPAddress]::Parse($currentIp)
if ($parsedIp.AddressFamily -ne [Net.Sockets.AddressFamily]::InterNetwork) { throw 'An IPv4 address is required.' }
$rules = Invoke-IsolationAws -Arguments @('ec2', 'describe-security-group-rules', '--filters', "Name=group-id,Values=$groupId")
$deviceRules = @($rules.SecurityGroupRules | Where-Object { -not $_.IsEgress -and $_.Description -eq $ruleDescription })
$managedRules = @($deviceRules | Where-Object { $_.IpProtocol -eq 'tcp' -and $_.FromPort -eq 22 -and $_.ToPort -eq 22 })
if ($deviceRules.Count -ne $managedRules.Count -or $managedRules.Count -gt 1) {
    throw "Expected at most one '$ruleDescription' SSH rule; refusing to change other rules."
}
# Devices on the same network share a public address, and AWS rejects a duplicate rule.
$alreadyAllowed = @($rules.SecurityGroupRules | Where-Object {
    -not $_.IsEgress -and $_.IpProtocol -eq 'tcp' -and $_.FromPort -le 22 -and $_.ToPort -ge 22 -and
    $_.CidrIpv4 -eq "$currentIp/32"
}).Count -gt 0
if ($alreadyAllowed) {
    Write-Host 'The current address is already allowed; no rule change.'
} elseif ($managedRules.Count -eq 0) {
    $permission = @(@{
        IpProtocol = 'tcp'; FromPort = 22; ToPort = 22
        IpRanges = @(@{ CidrIp = "$currentIp/32"; Description = $ruleDescription })
    })
    Invoke-IsolationAwsWithFile -Arguments @('ec2', 'authorize-security-group-ingress', '--group-id', $groupId) -Option '--ip-permissions' -Body $permission
    Write-Host "Created SSH rule '$ruleDescription'."
} elseif ($managedRules[0].CidrIpv4 -ne "$currentIp/32") {
    $request = @(@{
        SecurityGroupRuleId = $managedRules[0].SecurityGroupRuleId
        SecurityGroupRule = @{
            IpProtocol = 'tcp'; FromPort = 22; ToPort = 22
            CidrIpv4 = "$currentIp/32"; Description = $ruleDescription
        }
    })
    Invoke-IsolationAwsWithFile -Arguments @('ec2', 'modify-security-group-rules', '--group-id', $groupId) -Option '--security-group-rules' -Body $request
    Write-Host "Updated SSH rule '$ruleDescription' to the current address."
}
$instances = Invoke-IsolationAws -Arguments @('ec2', 'describe-instances', '--instance-ids', $instanceId)
$instance = $instances.Reservations[0].Instances[0]
if ($instance.State.Name -ne 'running') { throw "The instance is $($instance.State.Name), not running." }
$serverIp = $instance.PublicIpAddress
if (-not $serverIp) { throw 'The instance has no public IPv4 address.' }
Write-Host "SSH allowed for device '$Device'; server $serverIp."
if (-not (Test-SshPort -HostName $serverIp)) {
    # Some networks (VPN, mobile hotspot, campus NAT pools) leave through a different address than checkip reports.
    throw 'Port 22 is unreachable even though the rule matches this device. Check for VPN or a network whose outbound address changes per connection, then run again.'
}
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
