---
name: system-info
description: "System diagnostics and monitoring: check CPU, memory, disk usage, network status, running processes, system logs, and hardware info. Use when the user asks about system resources, performance, disk space, network connections, or system health."
metadata: {"pawbot":{"emoji":"📊","requires":{}}}
---

# System Info

System diagnostics via `exec` tool. Commands adapt to Linux/macOS/Windows.

## Quick Health Check

### Linux / macOS
```bash
echo "=== CPU ===" && top -bn1 | head -5
echo "=== Memory ===" && free -h 2>/dev/null || vm_stat
echo "=== Disk ===" && df -h /
echo "=== Load ===" && uptime
```

### Windows (PowerShell)
```powershell
Get-CimInstance Win32_Processor | Select-Object LoadPercentage
Get-CimInstance Win32_OperatingSystem | Select-Object FreePhysicalMemory, TotalVisibleMemorySize
Get-PSDrive -PSProvider FileSystem | Select-Object Name, @{N='Used(GB)';E={[math]::Round($_.Used/1GB,1)}}, @{N='Free(GB)';E={[math]::Round($_.Free/1GB,1)}}
```

## CPU

```bash
nproc                           # Number of cores
lscpu                           # Detailed CPU info
mpstat 1 5                      # CPU usage per second (5 samples)
```

## Memory

```bash
free -h                         # Human-readable memory
cat /proc/meminfo | head -5     # Detailed memory info
```

## Disk

```bash
df -h                           # Filesystem usage
du -sh /var/log/*               # Directory sizes
lsblk                           # Block devices
```

## Network

```bash
ip addr                         # Network interfaces (Linux)
ss -tlnp                        # Listening ports (Linux)
netstat -an | grep LISTEN       # Listening ports (cross-platform)
curl -s ifconfig.me             # Public IP
ping -c 3 google.com            # Connectivity check
```

## Processes

```bash
ps aux --sort=-%mem | head -15  # Top processes by memory
ps aux --sort=-%cpu | head -15  # Top processes by CPU
pgrep -la python                # Find Python processes
```

## Logs

```bash
journalctl -u nginx --since "1 hour ago" --no-pager  # Systemd service logs
tail -100 /var/log/syslog       # System log
dmesg | tail -30                # Kernel messages
```

## Tips

- On Windows, use PowerShell equivalents (`Get-Process`, `Get-CimInstance`)
- `htop` and `glances` are better interactive monitors if installed
- Use `watch -n 2 'command'` for live monitoring in terminal
