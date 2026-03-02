# Tool Usage Notes

Tool signatures are provided automatically via function calling.
This file documents non-obvious constraints, usage patterns, and best practices.

## exec — Shell Commands

- Commands have a configurable timeout (default 60s)
- Dangerous commands are blocked (rm -rf /, format, dd, shutdown, etc.)
- Output is truncated at 10,000 characters
- `restrictToWorkspace` config can limit file access to the workspace
- On Windows, use PowerShell syntax (`;` not `&&`, `Invoke-WebRequest` not `curl`)
- For long-running commands, use background execution

## read_file / write_file / edit_file — File Operations

- Always use absolute paths
- `edit_file` for modifying specific lines; `write_file` for replacing entire files
- Check encoding for non-UTF-8 files

## web_search — Web Lookups

- Requires a Brave Search API key in config
- Returns top 5 results by default
- Use for current events, latest docs, API references

## MCP Server Tools

### app_control (Screen Control)
- Prefix: `mcp_app_control_`
- Tools: `screen_read`, `screen_find`, `screen_wait`, `app_click`, `app_type`, `app_key`, `app_scroll`, `app_drag`, `app_launch`, `app_focus`, `app_close`, `clipboard_read`, `clipboard_write`, `clipboard_paste`, `template_save`, `template_list`
- Requires: `pyautogui`, `pytesseract`, `pillow` (`pip install pawbot[desktop]`)
- Always `screen_read()` first to see what's on screen before acting

### browser (Browser Automation)
- Prefix: `mcp_browser_`
- Tools: `browser_navigate`, `browser_click`, `browser_type`, `browser_screenshot`, `browser_extract`, `browser_evaluate`, `browser_wait`, `browser_scroll`
- Uses Playwright with anti-detection (stealth mode, randomized fingerprints)
- Always `browser_wait` after navigation before extracting content

### coding (Code Intelligence)
- Prefix: `mcp_coding_`
- Tools: `code_index_project`, `code_search`, `code_checkpoint`, `code_restore`, `code_list_checkpoints`, `code_scaffold`, `code_analyze`
- Always checkpoint before risky refactors

### deploy (Deployment)
- Prefix: `mcp_deploy_`
- Tools: `deploy_app`, `deploy_nginx_config`, `deploy_ssl_cert`, `deploy_db_migrate`, `deploy_db_backup`, `deploy_db_restore`, `deploy_rollback`, `deploy_status`
- Always backup database before migrations

### server_control (Server Management)
- Prefix: `mcp_server_control_`
- Tools: `server_status`, `server_service_status`, `server_service_restart`, `server_service_logs`, `server_port_check`, `server_process_list`, `server_process_kill`
- Run `server_status()` first for system overview

## cron — Scheduled Tasks

- Use skills/cron for full documentation
- `pawbot cron add --name "name" --message "msg" --cron "0 9 * * *"` for recurring
- `pawbot cron add --name "name" --message "msg" --at "2024-01-15T09:00:00"` for one-time
- Add `--deliver --to "USER_ID" --channel "telegram"` to send notifications
