---
name: file-search
description: "Search and navigate codebases efficiently using grep, ripgrep, find, fd, and tree. Use when the user asks to search code, find files, navigate project structure, or locate patterns across a codebase."
metadata: {"pawbot":{"emoji":"🔍","requires":{}}}
---

# File Search

Search files and navigate codebases using CLI tools via `exec`.

## Search File Contents

### ripgrep (fastest, preferred)
```bash
rg "pattern" --type py          # Search Python files
rg "TODO|FIXME" -g "*.js"      # Search JS files for TODOs
rg "function\s+\w+" --pcre2     # Regex search
rg "error" -l                   # List files only (no content)
rg "class.*Controller" -C 3    # Show 3 lines of context
rg "import" --count             # Count matches per file
```

### grep (universal fallback)
```bash
grep -rn "pattern" --include="*.py" .   # Recursive, line numbers
grep -rl "old_name" src/                # List files containing match
grep -rni "error" --include="*.log"     # Case-insensitive
```

## Find Files

### fd (fast find alternative)
```bash
fd "\.py$"                      # Find all Python files
fd "test" --type f              # Find files with "test" in name
fd --extension json --max-depth 2  # JSON files, max 2 levels deep
fd --changed-within 1d          # Files modified in last day
```

### find (universal)
```bash
find . -name "*.py" -type f     # All Python files
find . -name "*.log" -mtime -1  # Log files modified in last day
find . -type f -size +10M       # Files larger than 10MB
find . -empty -type f -delete   # Delete empty files
```

## Project Structure

```bash
tree -L 2 -I "node_modules|__pycache__|.git"  # 2 levels, ignore common dirs
tree --dirsfirst -L 3                          # Dirs first, 3 levels
```

## Combined Workflows

Find and replace across files:
```bash
rg -l "old_name" --type py | xargs sed -i 's/old_name/new_name/g'
```

Find large files:
```bash
find . -type f -exec du -h {} + | sort -rh | head -20
```

Count lines of code:
```bash
find . -name "*.py" -type f | xargs wc -l | tail -1
```

## Tips

- Prefer `rg` over `grep` — 10x faster, respects `.gitignore` by default
- Prefer `fd` over `find` — faster, simpler syntax, ignores hidden files
- Use `-l` flag for file-list-only mode (useful for piping)
- Add `--hidden` to include dotfiles/hidden dirs
