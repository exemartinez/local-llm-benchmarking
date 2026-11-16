[CmdletBinding()]
param([switch]$Json)

$ErrorActionPreference = 'Stop'

function Get-Field($Value) {
    if ($null -eq $Value) { return 'unknown' }
    return $Value
}

$os = Get-CimInstance Win32_OperatingSystem -ErrorAction SilentlyContinue
$computer = Get-CimInstance Win32_ComputerSystem -ErrorAction SilentlyContinue
$processors = @(Get-CimInstance Win32_Processor -ErrorAction SilentlyContinue)
$video = @(Get-CimInstance Win32_VideoController -ErrorAction SilentlyContinue)
$volumes = @(Get-CimInstance Win32_LogicalDisk -Filter 'DriveType=3' -ErrorAction SilentlyContinue)

$gpuDetails = @{}
$nvidia = Get-Command nvidia-smi.exe -ErrorAction SilentlyContinue
if ($nvidia) {
    try {
        $rows = & $nvidia.Source --query-gpu=name,memory.total,memory.used,utilization.gpu,driver_version --format=csv,noheader,nounits 2>$null
        $index = 0
        foreach ($row in $rows) {
            $parts = @($row -split ',\s*')
            if ($parts.Count -ge 5) {
                $gpuDetails[[string]$index] = @{ name = $parts[0]; vram_mib = [int64]$parts[1]; vram_used_mib = [int64]$parts[2]; utilization_percent = [int]$parts[3]; driver_version = $parts[4]; source = 'nvidia-smi' }
            }
            $index++
        }
    } catch { }
}

$gpus = @()
$gpuIndex = 0
foreach ($gpu in $video) {
    $detail = $gpuDetails[[string]$gpuIndex]
    $vram = 'unknown'
    $source = 'Win32_VideoController AdapterRAM unavailable or limited'
    if ($detail) {
        $vram = $detail.vram_mib
        $source = $detail.source
    } elseif ($gpu.AdapterRAM -and [int64]$gpu.AdapterRAM -lt 4294967295) {
        $vram = [math]::Round(([double]$gpu.AdapterRAM / 1MB), 0)
        $source = 'Win32_VideoController AdapterRAM'
    }
    $driver = if ($detail) { $detail.driver_version } else { Get-Field $gpu.DriverVersion }
    $name = if ($detail) { $detail.name } else { Get-Field $gpu.Name }
    $used = if ($detail) { $detail.vram_used_mib } else { 'unknown' }
    $utilization = if ($detail) { $detail.utilization_percent } else { 'unknown' }
    $gpus += [ordered]@{ name = $name; vram_mib = $vram; vram_used_mib = $used; utilization_percent = $utilization; driver_version = $driver; vram_source = $source }
    $gpuIndex++
}

$availableRamMiB = 'unknown'
$monitor = 'CIM Win32_OperatingSystem.FreePhysicalMemory'
if ($os -and $os.FreePhysicalMemory) { $availableRamMiB = [math]::Round(([double]$os.FreePhysicalMemory / 1024), 0) }
$counterAvailable = $false
try {
    $null = Get-Counter -Counter '\Memory\Available MBytes' -ErrorAction Stop
    $counterAvailable = $true
} catch { }

$snapshot = [ordered]@{
    schema_version = 1
    captured_at_utc = [DateTime]::UtcNow.ToString('o')
    platform = 'Windows'
    os = [ordered]@{ caption = (Get-Field $os.Caption); version = (Get-Field $os.Version); build = (Get-Field $os.BuildNumber); architecture = (Get-Field $os.OSArchitecture); windows_11_supported = [bool]($os -and [int]$os.BuildNumber -ge 22000) }
    cpu = [ordered]@{ name = (Get-Field (($processors | ForEach-Object Name | Select-Object -First 1))); logical_processors = (Get-Field $computer.NumberOfLogicalProcessors); cores = (Get-Field $computer.NumberOfCores); utilization_percent = if ($processors.Count -gt 0 -and $null -ne $processors[0].LoadPercentage) { $processors[0].LoadPercentage } else { 'unknown' } }
    memory = [ordered]@{ installed_mib = if ($computer.TotalPhysicalMemory) { [math]::Round(([double]$computer.TotalPhysicalMemory / 1MB), 0) } else { 'unknown' }; available_mib = $availableRamMiB; monitoring = $monitor }
    disks = @($volumes | ForEach-Object { [ordered]@{ size_gib = [math]::Round(([double]$_.Size / 1GB), 2); free_gib = [math]::Round(([double]$_.FreeSpace / 1GB), 2) } })
    gpus = $gpus
    monitoring_capabilities = [ordered]@{ get_counter_available_memory = $counterAvailable; nvidia_smi = [bool]$nvidia; vram_measurement = if ($gpuDetails.Count -gt 0) { 'nvidia-smi' } elseif ($gpus.Count -gt 0) { 'Win32_VideoController where credible; otherwise unknown' } else { 'unknown' } }
    privacy = 'Host name, user name, serial numbers, account identifiers and full file paths are intentionally omitted.'
}

if (-not $Json) { $snapshot | ConvertTo-Json -Depth 7 } else { $snapshot | ConvertTo-Json -Depth 7 -Compress }
