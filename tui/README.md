# The terminal front end

`adk-review-tui` is a screen for the terminal, built on [Textual](https://textual.textualize.io/). It picks a
repository, the branch to review and its target, the folders, the profile or the checks, and the outputs. Then it runs
the review and shows the report, the findings, the comment and the hand-off in tabs. A review it runs is the command's
own: the same checks, the same exit codes and the same files.

It is an application of its own on top of the reviewer, here in `tui/` with its own project file, a uv workspace
member beside the reviewer, and one lock for both. It imports the reviewer as any user would. A review never imports
it, and an install of the reviewer, a pipeline's among them, takes none of its code, its command or Textual.

## Install

In a checkout of adk-review:

```bash
uv sync --locked --all-packages   # the reviewer, the front end and Textual; make sync does the same
uv run adk-review-tui             # start it
```

`uv sync --locked` without `--all-packages` installs the reviewer alone, and removes the front end again: uv makes the
environment match the lock exactly.

As an installed command, with no checkout:

```bash
uv tool install --exclude-newer P3D "adk-review-tui @ git+<repository-url>#subdirectory=tui"
adk-review-tui
```

It installs the reviewer with it, from the same commit. `uv tool install` reads no lock, so `--exclude-newer P3D`
keeps the three days' cooldown the lock keeps.

In a pipeline, install the reviewer as [GUIDE.md](../GUIDE.md) says, with no extra: no UI package is installed.

## Its config

The screen's first line names the config it reads and the folders of repositories it lists. Where it reads it:

| Run from | Config |
|---|---|
| A checkout of adk-review | `tui/.tui.toml`, beside the front end, which git ignores |
| An installed copy, on Windows | `%APPDATA%\adk-review-tui\tui.toml` |
| An installed copy, on macOS and Linux | `~/.config/adk-review-tui/tui.toml`, or under `$XDG_CONFIG_HOME` |

An installed copy's config is in your own folder, which a reinstall or an uninstall leaves alone. Its state, the saved
reports, the cache and each review's files, is in your state folder in either case: `%LOCALAPPDATA%\adk-review-tui` on
Windows, `~/.local/state/adk-review-tui` elsewhere, or under `$XDG_STATE_HOME`.

The first run writes the config where it is missing, and names a folder of repositories for you: in a checkout, the
folder that holds the checkout, where its neighbours are; in an installed copy, the folder the command starts in. Change
`root_dirs` there to list your own, as `root_dirs = ["~/Projects"]`. `--init-config` writes the file and prints its
path, and `--config FILE` reads another file. A checkout is known by the reviewer's project file and
`tui/pyproject.toml`, so a ZIP download is one too.

A checkout that still holds the old config, `adk_review/tui/.tui.toml`, from when the front end lived in the reviewer's
package, or one in your own folder from an earlier version, gets the one you edited last, in `tui/.tui.toml`. The old
file moves, so one file holds the config; the one in your own folder stays, for an installed copy. Then the old folder
goes: its saved reviews move to your state folder, Python's old cache is removed, and so is every folder left empty. A
file there that the front end did not write stays, with its folder. The package's folder from when it was named
`adk_review_tui`, beside `tui/tui/`, goes the same way. `tui.example.toml`, in `tui/`, shows every setting.

| Setting | What it sets |
|---|---|
| `[workspace] root_dirs` | The folders that hold your repositories: a path, or `{ label = "...", path = "..." }`. With more than one, the screen asks for the folder first. |
| `scan_depth` | How many folders deep it looks for repositories, from 1 to 5. |
| `fetch_timeout_s` | How long a fetch may run, in seconds. |
| `fetch_on_select` | Whether picking a repository fetches the branches its remote got since: `true` unless set to `false`. |
| `output_dir` | Where each review's JSON report, comment and hand-off go: by default the state folder's `outputs`, and a relative path is read from the config's own folder. |
| `[defaults]` | The profile, the `--fail-on` bar, and which outputs and options are on. |
| `[projects."<name>"]` | One repository's saved choices, written at each review the screen runs. |

A `fail_on` that names no severity, a `root_dirs` that names no folder, a switch that is not `true` or `false`, a
number out of its range, a table or a setting the config does not know, or a file that is no TOML, stops the start
with one sentence that says what to write, and exit 2. A file saved with a byte order mark, as Notepad and
PowerShell 5 save one, is read.

The state folder holds the last report of each target, which marks findings new or resolved, and the review cache.
A target's last report moves on only where a review found other findings, so the same review run a third time is
served from the cache with no model call.

## The steps

1. The folder of repositories, where `root_dirs` names more than one.
2. The repository.
3. The branch under review.
4. The target branch.
5. The folders to include or leave out: `--path` and `--path-exclude`. Type any other path in the field below
   them, a comma or a new line between two.
6. How it reviews: the branch against its target, what is staged (`--staged`), or every file (`--all`); the profile
   or `--check`; `--fail-on`; `--diff-cap`.
7. The outputs: `--comment`, `--handoff`, `--verify`, the cache and `--previous`.

Each step is a panel. A panel you minimize leaves its row and docks into one line at the top, as a chip that names
its step and its choice, `▶ 2 Review: main`; a row with no open panel goes, so the results take the screen. On a
narrow screen the chips keep the step's number and its choice, and the step's name moves to the chip's tooltip.
Click a chip, or press its number, to restore that panel; `Ctrl+T` minimizes every panel and `Ctrl+O` restores them.

The line under the steps shows the command it will run, ready to paste. While a review runs, its progress shows in
the last tab. Its results stay with the target they came from: choose another branch, mode or repository, and the
tabs clear.

A finding is drawn as plain text, with its reason, "Why it matters", and its corrected code where there are
some: a rule's reason at any severity, a guideline's for a blocker or a major. A guideline's words are a model's, and
no link in them can be clicked.

## It writes nothing until you ask

As the reviewer does, the screen reads a repository, and writes into it only where you act:

- It lists the branches as the repository holds them, as of its last fetch. When you pick a repository, in its list
  or the command palette, it first runs `git fetch --no-prune`, which adds the branches the remote got since and
  removes none, then lists them. Picked again, the repository keeps the branches and options you chose. The
  repository the screen picks by itself as it opens is not fetched. `fetch_on_select = false` turns this off.
- F5 runs `git fetch --prune` on the repository you chose: it also removes the branches the remote no longer holds.
- A fetch that fails, offline or past `fetch_timeout_s`, leaves the lists as the repository holds them, and says so.
- While a repository's branches and folders load, a review waits, and says so. One fetch of a repository runs at a
  time, its linked worktrees with it, and one asked for meanwhile takes its answer. A fetch starts none of git's own
  maintenance. Quitting waits for a fetch that is running, at most `fetch_timeout_s`: git stopped midway can leave
  its locks behind.
- A remote's branch, `origin/<branch>`, is reviewed as the remote holds it, even where a local copy of it is ahead
  or behind. No local branch is made.
- For a local branch behind its remote, it asks. On yes, `git branch -f` moves that branch and writes nothing else,
  but never one a working tree has checked out: that one stays as it is, and the remote's branch is reviewed.
- Each read runs as the reviewer runs git: a repository's own config starts no program, and no read takes a lock.

## Keys

| Key | What it does |
|---|---|
| `Ctrl+R` | Run the review |
| `Ctrl+D` | Run a `--dry-run`: what the review would cover and run, nothing run |
| `Ctrl+X` | Stop a running review |
| `Ctrl+T` | Minimize every panel |
| `Ctrl+O` | Restore every panel |
| `Ctrl+Y` | Copy the open tab: the report, a finding, the comment or the hand-off |
| `Ctrl+P` | Open the command palette: a repository, a branch, a profile or an action by name |
| `F5` | Fetch the chosen repository with `--prune` and list its branches again, the branches you chose kept where they are still there |
| A number key | Minimize or restore the panel of that number, where no text field has the focus |
| `q` | Quit |

The action keys work wherever the focus is, a text field included. While a dialog is open, only `Ctrl+X` works,
and while a review runs, `Ctrl+R` and `Ctrl+D` wait for it to end: they never restart it.

## On Windows

It runs in cmd, in PowerShell and in Windows Terminal alike.

- The command it shows reads the same in cmd and in PowerShell. Where an argument holds a character one of them
  reads, such as `$`, `%` or `!`, it says the command cannot be pasted, and `Ctrl+R` still runs it.
- `Ctrl+Y` copies into Windows' own clipboard, so it works in the classic console window too. Elsewhere it copies
  through the terminal: Windows Terminal, iTerm2 and kitty take it, and macOS Terminal does not.
- No key is one a terminal sends as another key, as it sends Enter for `Ctrl+M`, or one the classic console keeps for
  itself: `Ctrl+A`, `Ctrl+C`, `Ctrl+F`, `Ctrl+M` and `Ctrl+V`.
- A review it runs writes its text in UTF-8, so a branch's or a file's name reads as written.
- Stopping a review stops its whole process tree.

## Its modules

| Module | What it holds |
|---|---|
| `__init__.py`, `__main__.py` | The `adk-review-tui` command: `--config`, `--init-config`, and the config read before the screen opens |
| `app.py` | The screen: the steps, the tabs, the keys, and the review it runs |
| `config.py` | `tui.toml` and where it and the state live: the folders of repositories, the defaults and each repository's saved choices |
| `git_sync.py` | The repositories under a folder, their branches and how each stands to its remote, and the fetch |
| `options.py` | The profiles, the command a selection makes, and the report `--previous` reads |
| `findings.py` | A `--json` report read for the findings tab, its filters, and a check's explanation |
| `modals.py` | The dialogs: a branch behind or apart from its remote, and a check's explanation |
| `commands.py` | The command palette |
| `clipboard.py` | Copying, through the terminal and through Windows' own clipboard |
| `styles.py` | The screen's styles, and `literal`, which draws a `[` as written |

The rules for changing this folder are in [CLAUDE.md](../CLAUDE.md), under "Front end".
