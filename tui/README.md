# The terminal front end

`adk-review-tui` is a screen for the terminal, built on [Textual](https://textual.textualize.io/). It picks a
repository, the branch to review and its target, the folders, the profile or the checks, and the outputs. Then it runs
the review and shows the report, the findings, the comment and the hand-off in tabs. A review it runs is the command's
own: the same checks, the same exit codes and the same files. On your key it also opens a finding at its lines in an
Azure DevOps pull request, and posts the comment you edited to that line: "Comment on an Azure DevOps pull request",
below, says how.

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
reports, the cache, each review's files and what it keeps of Azure DevOps, is in your state folder in either case:
`%LOCALAPPDATA%\adk-review-tui` on Windows, `~/.local/state/adk-review-tui` elsewhere, or under `$XDG_STATE_HOME`.

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
| `fetch_timeout_s` | How long a fetch may run, in seconds, from 3 to 120. |
| `fetch_on_select` | Whether picking a repository fetches the branches its remote got since: `true` unless set to `false`. |
| `output_dir` | Where each review's JSON report, comment and hand-off go: by default the state folder's `outputs`, and a relative path is read from the config's own folder. A review makes the folder as it starts, and says so where it cannot. |
| `[defaults]` | The profile, the `--fail-on` bar, and which outputs and options are on. |
| `[projects."<name>"]` | One repository's saved choices, written at each review the screen runs. |

A `fail_on` that names no severity, a `profile` the reviewer has not, a `root_dirs` that names no folder, a switch
that is not `true` or `false`, a number out of its range, a table or a setting the config does not know, or a file
that is no TOML, stops the start with one sentence that says what to write, and exit 2. A file saved with a byte
order mark, as Notepad and PowerShell 5 save one, is read.

The state folder holds the last report of each target, which marks findings new or resolved, the review cache, and
`azure.json`: the pull request's address you gave for each branch, and the thread each finding was posted as.
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

Before you run a review, the banner says what it would read, as the review's own collector reads it: the commits
since the merge base with the target, and the changed files, how many the change added, modified, deleted or
renamed. Where the review would stop before it reads, as for two branches that share no history, the banner gives
the review's own sentence. The review's log names the same commits. Its count of the files it read can be lower:
it counts apart the files of a kind `[review] scan` does not name, which only the credential rules read, those under
a third-party folder, and those outside `--path` or under `--path-exclude`, and names how many.

Each step is a panel. A panel you minimize leaves its row and docks into one line at the top, as a chip that names
its step and its choice, `▶ 2 Review: main`; a row with no open panel goes, so the results take the screen. On a
narrow screen the chips keep the step's number and its choice, and the step's name moves to the chip's tooltip.
A dialog wider than the screen, the comment's editor at macOS Terminal's 80 columns among them, takes the screen's
width, its buttons in view.
Click a chip, or press its number, to restore that panel; `Ctrl+T` minimizes every panel and `Ctrl+O` restores them.

The line under the steps shows the command it will run, ready to paste. While a review runs, its progress shows in
the last tab. Its results stay with the target they came from: choose another branch, mode or repository, and the
tabs clear. The comment and the hand-off tabs show what the last review wrote, or that it wrote none. A dry run, and
a review the reviewer refused, decide nothing and write no report: each leaves them and the findings as they are. A run
that ends with no report shows what it said in the Report tab, and its last line in the banner, as a review refused
for an arm with no login does. The banner names the verdict a review reached, and the Findings tab of a review that
could not check everything says so: in its name, `(2, DEGRADED)`, and where it kept no finding, never that it found
none. F5, or the same branch picked again, keeps the review this session ran of it over an older saved one, until
you choose another target: back on it, the saved one shows, since a review that could not check everything is never
saved. A review that writes its report minimizes every panel, so its results take the screen; `Ctrl+O` restores
them. A dry run, or a review refused, leaves them open, the step to change among them. A review you stop says so in
the Report tab, and that is what `Ctrl+Y` copies from it.

A finding is drawn as plain text, with its reason, "Why it matters", and its corrected code where there are
some: a reason at any severity, and the corrected code where a model gave one. A guideline's words are a model's, and
no link in them can be clicked: the comment and the hand-off, drawn as Markdown, show an address as a link and open
none.

## Comment on an Azure DevOps pull request

A finding of the Findings tab opens at its place in Azure DevOps, and its comment is posted to that file and line,
when you ask. The reviewer itself posts nothing, as before: this is the screen's one write outside your machine.

1. Log in once, in a terminal: `az login`. The screen asks the Azure CLI for a token when you post, and stores none.
2. Review the pull request's branch, as any branch.
3. Paste the pull request's address into the field above the findings, as the browser shows it:
   `https://dev.azure.com/<organization>/<project>/_git/<repository>/pullrequest/<number>`. It is kept for that
   repository and branch, so you paste it once.
4. Choose a finding, then press a key:

| Key | What opens |
|---|---|
| `o` | The finding's lines, highlighted, in the file as the commit reviewed holds it |
| `p` | The pull request at the finding's file, or at its comment once posted |
| `c` | The comment's draft, in an editor |

`o` and `p` also show the link in the line under the steps, to copy where no browser opens, as over SSH. The
address field takes nothing while a repository's branches load. No comment opens while a review runs, so in the
editor `Ctrl+X` is always its own cut.

The draft is the finding's own words: its severity and title, its reason, what to change, and its corrected code.
Edit it as you like: you are its author. Where the review's report calls the code `suggestable`, it is a `suggestion`
block, which the pull request's author applies with one click. The report says so only where every line the finding
stands on is one the change added, its quote holds no deleted line, and the code is the model's own, with nothing
cut, hidden or changed by the report. Any other code is shown and cannot be applied. The editor shows the lines your
comment stands on, which a suggestion replaces: check that it fits, or delete the block. The last line names the
tool, the check and the finding. Keep it: it finds the comment again. A comment with the same words is found too, so
a finding is not posted twice, by you or by a teammate. A report written before this field was added drafts no
suggestion: review the branch again.

**Post** asks once more, naming the file, the lines and the pull request. Then one comment is posted, as you, on
those lines. **Copy** gives you the text to paste by hand. Nothing is sent before the confirmation, and never more
than one comment a click.

A post is refused, with what to do next, where:

| What the screen says | What to do |
|---|---|
| Azure DevOps did not accept your login | Run `az login`, or `az login --tenant <id>` where it names one, then post again |
| Your account may not comment here | Ask for the "Contribute to pull requests" permission |
| The pull request has a newer push | Fetch, review the branch again, then comment: the lines may have moved |
| The pull request never held the commit | Check the address, and the branch you reviewed |
| The pull request does not change the file | Nothing: no line there can hold a comment |
| The pull request is completed or abandoned | Nothing: it takes no new comment from here |
| This clone does not hold the reviewed commit's file | Fetch, then press `c` again |
| The comment may be posted | Press `c` again: the answer did not come back, and a comment that arrived is found, with no second one sent |

Your edit is kept after a refusal, and when you close the editor: press `c` again. To start from the draft again,
empty the editor and close it.

What it sends and keeps:

- It calls `dev.azure.com`, or your organization's older `visualstudio.com` address, and no other host. An address
  on any other host is refused before a token is asked for. Azure DevOps Server is not read yet.
- The token comes from `az account get-access-token`, stays in memory, and is asked again when it expires. A login
  Azure DevOps refuses is forgotten, so the next post asks `az` again.
- `azure.json` in the state folder holds the address you gave for each branch, and the thread each finding was
  posted as. You may delete it: a finding posted before is found by its comment's last line. Where you deleted a
  comment in Azure DevOps, press `c` on its finding: the screen asks the pull request, and lets you post it again.
- A company proxy's certificate is trusted where your system trusts it, as for the reviewer. `SSL_CERT_FILE` names a
  bundle where it does not.

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
- A comment is posted to an Azure DevOps pull request only when you press Post and confirm, one a click: the
  section above says what is sent and where.

## Keys

| Key | What it does |
|---|---|
| `Ctrl+R` | Run the review |
| `Ctrl+D` | Run a `--dry-run`: what the review would cover and run, nothing run |
| `Ctrl+X` | Stop a running review. With no review running, it cuts the chosen text in a text field |
| `Ctrl+T` | Minimize every panel |
| `Ctrl+O` | Restore every panel |
| `Ctrl+Y` | Copy the open tab: the report, the chosen finding, the comment, the hand-off or the progress lines. A tab with nothing to copy says so |
| `Ctrl+Q` | Quit, whatever runs, a dialog open or a comment not posted: Textual's own key. VS Code's terminal keeps it for itself, so use `q` there |
| `Ctrl+P` | Open the command palette: a repository, any of its branches, a profile or an action by name |
| `F5` | Fetch the chosen repository with `--prune` and list its branches again, the branches you chose kept where they are still there |
| A number key | Minimize or restore the panel of that number, where no text field has the focus |
| `o` | Open the chosen finding's lines in Azure DevOps, in the file as the commit reviewed holds it |
| `p` | Open the pull request at the chosen finding: its comment thread once posted, else its file in the Files tab |
| `c` | Comment on the chosen finding in its pull request: a draft to edit, a confirmation, then one post |
| `q` | Quit, where no review runs and no text field has the focus. Over a comment not posted yet it asks first: a second `q` within 5 seconds quits without it. While a review runs, it says to stop it first |

The action keys work wherever the focus is, a text field included. `Ctrl+X` stops a review while one runs, and only
then: with none running it is a text field's own cut. While a dialog is open, only `Ctrl+X` and `Ctrl+Q` work, and
while a review runs, `Ctrl+R` and `Ctrl+D` wait for it to end: they never restart it. The line under the screen
shows the keys that act now: Stop while a review runs, Run and Dry Run while none does. `o`, `p` and `c` are plain
letters: they act on the Findings tab alone, where no text field has the focus, and they need the pull request's
address in the field above the table.

`Ctrl+Y` and the comment editor's Copy send the text through the terminal, which Windows Terminal, iTerm2 and kitty
take. On Windows and on a Mac they also put it into the system's own clipboard, so they work in the classic console
window and in macOS Terminal too. On a Mac over SSH the terminal alone takes it: the Mac's clipboard is then the far
machine's.

## On Windows

It runs in cmd, in PowerShell and in Windows Terminal alike.

- The command it shows reads the same in cmd and in PowerShell. Where an argument holds a character one of them
  reads, such as `$`, `%` or `!`, it says the command cannot be pasted, and `Ctrl+R` still runs it.
- `Ctrl+Y` copies into Windows' own clipboard, so it works in the classic console window too.
- No key is one a terminal sends as another key, as it sends Enter for `Ctrl+M`, or one the classic console keeps for
  itself: `Ctrl+A`, `Ctrl+C`, `Ctrl+F`, `Ctrl+M` and `Ctrl+V`.
- A review it runs writes its text in UTF-8, so a branch's or a file's name reads as written.
- Stopping a review stops its whole process tree.
- The Azure CLI is `az.cmd` there: it is found on PATH and run with fixed arguments alone, and one that does not
  answer is stopped with its whole process tree.

## Its modules

| Module | What it holds |
|---|---|
| `__init__.py`, `__main__.py` | The `adk-review-tui` command: `--config`, `--init-config`, and the config read before the screen opens |
| `app.py` | The screen: the steps, the tabs, the keys, and the review it runs |
| `config.py` | `tui.toml` and where it and the state live: the folders of repositories, the defaults and each repository's saved choices |
| `git_sync.py` | The repositories under a folder, their branches and how each stands to its remote, the fetch, the change a review of a pick reads, through the review's own collector, a file's lines at a commit, and the one runner that stops a program whole past its timeout, git or the Azure CLI |
| `options.py` | The profiles, the command a selection makes, the folder a review's files go to, and the report `--previous` reads |
| `findings.py` | A `--json` report read for the findings tab, with the commit it reviewed, its filters, and a check's explanation |
| `azure.py` | Azure DevOps, with no call made: a pull request's address read, a finding's links built, the comment drafted from a finding, with the mark that finds it again, the thread it is posted as, and which of a pull request's threads is a finding's already |
| `azure_client.py` | The calls to Azure DevOps and their login: a token from `az`, with fixed arguments, kept in memory alone, and one comment's post, with the checks before it |
| `azure_state.py` | `azure.json` in the state folder: the pull request's address given for a branch, and the thread each finding was posted as |
| `modals.py` | The dialogs: a branch behind or apart from its remote, a check's explanation, and a comment's editor and the question before its post |
| `commands.py` | The command palette |
| `clipboard.py` | Copying, through the terminal and through the system's own clipboard on Windows and on a Mac |
| `styles.py` | The screen's styles, and `literal`, which draws a `[` as written |

The rules for changing this folder are in [CLAUDE.md](../CLAUDE.md), under "Front end".
