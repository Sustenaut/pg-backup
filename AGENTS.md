# Working in this repository

Managed by pubkit 0.36.0: `pubkit sync` rewrites this file and `pubkit
sync --check` fails when it differs, so every pub* repository gives agents
and people the same rules. Repository-specific guidance lives in the
README. The standard behind these rules is pubkit's README:
<https://github.com/Sustenaut/pubkit#readme>.

## Pull requests: ready, or draft

- Open a pull request **ready for review** by default. Merging is gated by
  the checks main's ruleset requires (`ci / passed`, and `release /
  release` where pubrel releases), and `ci / passed` fails when any CI job
  does, SonarQube Cloud's quality gate included. Draft adds no safety.
- Mark it **draft** only when something else must happen first: another
  pull request merged, a release published, a pin moved, a person's
  decision or action. Say in its description what it waits on, and mark it
  ready once that is done.
- A stacked pull request says so in its title and description.

## Branches: up to date with main

- Before each push, `git fetch origin main` and bring `origin/main` into
  the branch, so a pull request arrives as up to date as main's ruleset
  requires and a conflict shows while someone is working on the branch.
- A branch never pushed: `git rebase origin/main`. A branch already pushed,
  or with a pull request: `git merge origin/main`, which needs no force
  push and leaves reviewers' views where they were.
- A conflict: resolve it when the resolution is clear; otherwise stop,
  push nothing half-done, and say what conflicts.
- A folder that only follows main fast-forwards to `origin/main`. Never
  sync on a timer: the rule runs when the branch gets new code.
