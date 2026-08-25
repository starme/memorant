---
description: Migrate legacy bugs/snippets/daily/arch notes into Memorant memories
allowed-tools: mcp__plugin_memorant_memorant__memorant_migrate
---

Migrate legacy notes into Memory Envelopes: **$ARGUMENTS**

1. Call `mcp__plugin_memorant_memorant__memorant_migrate` with `dry_run=true` to preview what would migrate.
2. Review the preview; confirm with the user before actually migrating.
3. On confirmation, call `mcp__plugin_memorant_memorant__memorant_migrate` with `confirm=true`.
4. Summarize the migration report (backup path, migrated/idempotent-skipped/no-form-skipped/conflict/failed counts).

Migration is idempotent and never overwrites existing files; the original notes are backed up to `.memorant/migration-backup/<timestamp>/` (kept permanently for rollback).
