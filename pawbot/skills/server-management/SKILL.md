---
name: server-management
description: "Monitor and manage servers via the server_control MCP server: check system resources (CPU, RAM, disk), manage systemd services, view logs, check ports, and restart processes. Use when the user asks about server status, service management, resource monitoring, or process control."
metadata: {"pawbot":{"emoji":"🖧","requires":{}}}
---

# Server Management

Use the `server_control` MCP server for system monitoring and service management.

## Core Tools

| Tool | Purpose |
|------|---------|
| `server_status` | Full system snapshot: CPU, memory, disk, load |
| `server_service_status` | Check status of a systemd service |
| `server_service_restart` | Restart a systemd service |
| `server_service_logs` | View recent logs for a service |
| `server_port_check` | Check if a port is in use and by what process |
| `server_process_list` | List running processes (top-like) |
| `server_process_kill` | Kill a process by PID or name |

## Workflow: Diagnose a Slow Server

```
mcp_server_control_server_status()
mcp_server_control_server_process_list(sort_by="cpu", limit=10)
mcp_server_control_server_port_check(port=8080)
```

## Workflow: Restart a Service

```
mcp_server_control_server_service_status(service="nginx")
mcp_server_control_server_service_restart(service="nginx")
mcp_server_control_server_service_logs(service="nginx", lines=50)
```

## Tips

- Run `server_status` first to get an overview of system health
- Check logs after restarting services to verify they came up cleanly
- Use `process_list(sort_by="memory")` to find memory hogs
- `port_check` helps diagnose "address already in use" errors
