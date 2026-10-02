# 📋 backlogger

**Version 2.1.0 — backlog doctor**

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
bl doctor                   # check backlog, updates, instructions, and API keys
bl doctor --fix              # offer repairs, with confirmation
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

## 🩺 Doctor

`bl doctor` checks the current directory without changing the backlog or agent
files (it may create the normal `.backlog.lock` sidecar). Unlike ordinary reads,
it does **not** migrate legacy tasks automatically.

- **Backlog:** missing/duplicate IDs across active and archived tasks, malformed
  task rows, unknown statuses, empty descriptions, unfinished archived tasks,
  merge conflicts, and read/write/locking problems. Completed active tasks get
  an optional `bl purge` suggestion; in-progress tasks are never assumed stale.
- **bl updates:** compares this version with `VERSION` in the GitHub `main`
  script, without executing downloaded code. If newer, prints the install
  command. `bl install` alone copies the running version; it does not fetch an
  update. Updates are never installed automatically.
- **Agent instructions:** checks existing `AGENTS.md` and `CLAUDE.md` against
  this bl version's instructions and recommends `bl setup` if missing, outdated,
  or duplicated. After updating bl, run `bl setup` again.
- **Keys and balance/quota:** checks `OPENAI_API_KEY` and `ANTHROPIC_API_KEY`
  separately with **one small generation request per configured key**, using the
  same model and 16-output-token limit as smart IDs. A models-list GET alone can
  succeed with an empty balance. The probe uses fixed synthetic text, never
  backlog content; keys, generated IDs, and response bodies are never printed.
  **Standard API charges may apply**; `--offline` skips these requests. Success
  means generation is usable now, not that the exact remaining balance is known.
  Agent CLI logins are not API keys. Missing keys are optional, not fatal.

An explicit insufficient-balance/quota response (for example OpenAI's HTTP 429
`insufficient_quota` / `credit_balance_exhausted`, or Anthropic's low-credit
response) is an **error**, with a billing/top-up suggestion. A temporary rate
limit is a warning to retry, not evidence of an empty balance. Unknown 429 bodies
leave the balance unverified. There are no retries or automatic provider switches;
`bl add` still prefers OpenAI when both keys exist.

```sh
bl doctor --fix             # review and confirm safe backlog/setup repairs
bl doctor --fix --yes       # explicitly accept those repairs without prompts
bl doctor --offline         # no network; report only local key presence
bl doctor --help
```

Safe backlog repairs append missing IDs, keep the first occurrence of a duplicate
ID and reassign later occurrences, normalize `[X]` to `[x]`, and restore unfinished
archived tasks to the active section without changing their status. The exact
changes are shown before confirmation. Repairs use the usual lock and atomic
replacement; if the backlog changes while you're deciding, nothing is overwritten
and you must rerun doctor. No lock is held during prompts or network requests.
Merge conflicts block automatic backlog repairs; ambiguous statuses and malformed
rows need an explicit decision. `--fix` can also run `bl setup` for existing agent
files; if neither file exists, run `bl setup` to choose which one to create.

Without a TTY, `--fix` does not apply changes unless `--yes` is supplied. Network
requests have short timeouts; unavailable checks are reported as unknown, not as
invalid keys or “up to date”. Exit status is **1** for detected errors remaining
after repairs, **0** otherwise (warnings, including network failures and missing
optional keys, do not fail the command).

## 🧠 Optional readable IDs

Without API keys, generated IDs are random 5–6 character strings. With
`OPENAI_API_KEY` or `ANTHROPIC_API_KEY`, `bl` asks one provider for a short
readable candidate before taking the backlog lock. OpenAI is preferred when both
keys are present.

The model is instructed to return one 3–8 character ID matching the required
ASCII format. It may shorten words by dropping vowels (for example, `SCORE` →
`SCR`) to keep IDs recognizable and compact; invalid output is rejected rather
than guessed. Only the bounded new-task description is sent. The backlog and
API key are not included in diagnostics or Markdown. Timeouts, invalid
responses, provider errors, and collisions fall back locally, so adding a task
never depends on the provider.
`bl doctor` distinguishes invalid generated IDs from connection/authentication
failures. When an API key is configured, `bl setup` may send one request per
existing `AGENTS.md`/`CLAUDE.md` file (each capped at 24 KiB total) containing
its line-numbered content and the standard bl policy. The selected provider
assesses existing guidance and chooses a nearby insertion point. The model can
return only a bounded assessment and line number; it cannot edit or remove
project text. If no key is configured or a request fails, setup uses a local
placement heuristic. Standard API charges may apply.
Quote explicit IDs in the shell, for example `bl add 'Ship it #shipIt'`.

## 🖥️ GUI

`bl gui` opens an embedded native macOS app (Swift and the Xcode Command Line
Tools are required). GUI actions call the same Python transaction backend as the
CLI. Selection follows stable IDs, stale status/text edits are rejected, and
adding stays responsive while an optional ID provider is running.

---

The backlog is always one task away from being empty. 😅
