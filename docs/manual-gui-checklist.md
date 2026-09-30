# macOS GUI verification

Run these checks from a temporary directory with the current `bl`:

- `bl gui` opens and lists tasks with visible stable `#id` values; IDs have a distinct accent highlight and long descriptions wrap without truncation.
- Space opens Add Task; Cmd+Enter saves; Esc cancels; Cmd+A works with a non-Latin keyboard layout.
- Double-click and `E` open Edit Task. A concurrent CLI text edit shows the latest text, reopens the unsaved draft, and allows a reconciled retry.
- Enter claims/releases the selected task. A concurrent status change is rejected instead of toggling stale state.
- Ctrl+Up/Down moves the selected task and keeps selection on the same ID.
- Archive invokes the shared purge transaction; clicking Backlog Zero shows completed tasks.
- While a mocked/delayed ID provider handles Add Task, the main window remains responsive.
- While another process holds `.backlog.lock`, list/status/edit/move/archive waits remain off the AppKit main thread.
- Two simultaneous `bl gui` launches work independently and do not share a temporary Swift source path.
- Cmd+Q, Esc in the main window, and closing the window terminate the app.
