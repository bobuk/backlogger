# 📋 backlogger

A tiny backlog manager. One file, one `backlog.md`, zero dependencies.

## 🚀 Install

```sh
curl -sL https://raw.githubusercontent.com/bobuk/backlogger/main/bl | python3
```

Installs `bl` into `~/.local/bin`.

## ✨ Use

```sh
bl              # show the backlog
bl add <text>   # add a task (or just `bl a` and type it)
bl take <n>     # start a task
bl done <n>     # finish a task
bl available    # show what's free to grab
bl purge        # archive finished tasks
bl setup        # tell your AI agent about bl
```

Tasks live in `backlog.md` as plain Markdown:

```markdown
- [ ] todo
- [>] in progress
- [x] done
```

## 🤝 Why

Made for humans and AI agents to share one honest list. 🤖🧑

---

My backlog has only one item left: write a joke for this README. 😅
