---
name: git-workflow
description: "Advanced git workflows: branching strategies, interactive rebase, cherry-picking, conflict resolution, worktrees, stashing, bisect, and reflog recovery. Use when the user asks about git branching, rebasing, resolving merge conflicts, recovering lost commits, or managing multiple working trees."
metadata: {"pawbot":{"emoji":"🔀","requires":{"bins":["git"]}}}
---

# Git Workflow

Advanced git operations beyond basic add/commit/push.

## Branching

Create and switch:
```bash
git switch -c feature/my-feature
```

List with details:
```bash
git branch -vv
```

Delete merged branches:
```bash
git branch --merged main | grep -v main | xargs git branch -d
```

## Interactive Rebase

Squash last N commits:
```bash
git rebase -i HEAD~3
```

Rebase onto main (clean up before merge):
```bash
git fetch origin
git rebase origin/main
```

## Cherry-Pick

Apply a specific commit to current branch:
```bash
git cherry-pick <commit-hash>
```

Cherry-pick without committing (stage only):
```bash
git cherry-pick --no-commit <commit-hash>
```

## Conflict Resolution

After a rebase or merge conflict:
```bash
git status                    # See conflicted files
# Edit files, resolve markers (<<<<<<<, =======, >>>>>>>)
git add <resolved-files>
git rebase --continue         # or: git merge --continue
```

Abort if things go wrong:
```bash
git rebase --abort
git merge --abort
```

## Worktrees

Work on multiple branches simultaneously:
```bash
git worktree add ../my-feature feature/my-feature
git worktree list
git worktree remove ../my-feature
```

## Stashing

Save and restore work-in-progress:
```bash
git stash push -m "WIP: description"
git stash list
git stash pop                 # Apply and remove
git stash apply stash@{1}    # Apply without removing
```

## Recovery

Find lost commits with reflog:
```bash
git reflog
git checkout <lost-commit-hash>
```

Find which commit introduced a bug:
```bash
git bisect start
git bisect bad                # Current commit is bad
git bisect good <known-good>  # Known good commit
# Test and mark each: git bisect good / git bisect bad
git bisect reset
```

## Tips

- Rebase for linear history, merge for preserving branch topology
- Always `git fetch` before rebasing onto remote branches
- Use `git stash` before switching branches with uncommitted changes
- `git reflog` is your safety net — commits are never truly lost for ~90 days
