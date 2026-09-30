# 📋 backlogger

**Version 2.0.0 — major backlog-format change**

A tiny shared backlog. One script, one Markdown file, zero dependencies.

## ⚠️ Version 2.0.0 format change

Version 2.0.0 appends a stable `#id` to every task row and automatically assigns
IDs to existing active and archived tasks on first access. This is a major format
and writer-protocol change: use only `bl` 2.x for every operation on a shared
backlog. Older versions do not participate in locking and can overwrite
concurrent changes. Check the installed version with `bl --version`.

## 🚀 Install

```sh
curl -sL https://raw.githubusercontent.com/bobuk/backlogger/main/bl | python3
```

Installs `bl` into the first common local bin directory already on `PATH`.

## ✨ Use

```sh
bl                         # show active tasks and their stable IDs
bl add "Fix login #login"  # add a task with an explicit ID
bl add "Fix login"         # add with a generated ID
bl take login               # atomically claim a free task
bl done login               # finish it
bl untake login             # release it
bl available                # show what's free to claim
bl completed                # inspect completed active/archive tasks
bl edit login "New text"    # edit by stable ID
bl move login up            # reorder by stable ID
bl gui                      # open the native macOS app
bl purge                    # archive finished tasks
bl run                      # ask your configured AI agent to work
bl setup                    # install strict agent instructions
```

Tasks stay readable in `backlog.md`:

```markdown
- [ ] todo #todo1
- [>] in progress #work1
- [x] done #done1
```

Use the final visible ID without `#` for `take`, `done`, and `untake`. Numeric
arguments still work, but they mean the **current physical line** and can become
stale after another writer changes the file. Prefer IDs.

## 🔒 Sharing safely

Every current `bl` CLI and GUI operation uses the same bounded sidecar lock,
fresh read, validation, and atomic replacement. Two agents cannot both
successfully claim the same free task, and cooperating writes are not lost.

Use `bl` as the **only** way to read or modify `backlog.md`. The public
`completed`, `edit`, and `move` commands cover the corresponding GUI actions for
agents. Direct edits, old `bl` versions, separate clones/worktrees, network
filesystems, and sudden power loss are outside these guarantees. Concurrent
transactions support local macOS and Linux filesystems. Existing mode bits are
preserved; ACLs, ownership, and extended attributes are not guaranteed.

Old task rows receive random stable IDs on first access. Existing explicit IDs
are preserved. Duplicate IDs fail with their locations instead of being repaired
silently.

## 🧠 Optional readable IDs

Without API keys, generated IDs are random 5–6 character strings. With
`OPENAI_API_KEY` or `ANTHROPIC_API_KEY`, `bl` asks one provider for a short
readable candidate before taking the backlog lock. OpenAI is preferred when both
keys are present.

Only the bounded new-task description is sent. The backlog and API key are not
included in diagnostics or Markdown. Timeouts, invalid responses, provider
errors, and collisions fall back locally, so adding a task never depends on the
provider. Quote explicit IDs in the shell, for example `bl add 'Ship it #shipIt'`.

## 🖥️ GUI

`bl gui` opens an embedded native macOS app (Swift and the Xcode Command Line
Tools are required). GUI actions call the same Python transaction backend as the
CLI. Selection follows stable IDs, stale status/text edits are rejected, and
adding stays responsive while an optional ID provider is running.

---

The backlog is always one task away from being empty. 😅
