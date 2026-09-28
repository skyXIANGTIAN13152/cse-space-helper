# CSE Space Helper

[简体中文](README.md) · [Download both editions](https://github.com/skyXIANGTIAN13152/cse-space-helper/releases/latest)

An independent Windows utility for UNSW CSE accounts. Check your home-directory quota, review eligible old VS Code components, and clean the selected items. This is not an official UNSW application.

## Download and first use

- `CSE-Space-Helper-en.exe`: fully English interface, help, errors, and reports.
- `CSE-Space-Helper-zh.exe`: Simplified Chinese edition.
- Windows 10/11 x64. Python and SSH dependencies are included.
- Your account must have CSE SSH access. On a new computer, first run `ssh YOUR_ZID@login.cse.unsw.edu.au` in the system terminal, verify the host identity, and complete a login. The app uses the resulting `~/.ssh/known_hosts` record and refuses unknown or changed host keys.

## Everyday use

1. Close VS Code connections to the account on **all** your computers.
2. Open the app, enter your own zID and password, and click **Connect & scan**.
3. Review the checked items and their reasons. Click any row to deselect it.
4. Click **Clean selected**. The app rechecks eligibility before deleting, then checks the quota and tests writing a small temporary file.
5. Close the app to disconnect. It does not clean on a schedule, start with Windows, or terminate your work processes.

The password is used for the current SSH connection only, cleared from the input box, and never written to reports, configuration, or program files. Reports are saved locally under `%LOCALAPPDATA%\CSESpaceHelper\reports`; they contain account names, paths, sizes, and results. **Export report** saves a readable copy to a location you choose.

## Cleanup scope

Only verified items under `~/.vscode-server` are eligible:

| Item | Rule |
|---|---|
| Old remote servers | Preserve the locally matching, latest complete, and detected running versions |
| Old launchers | Require a complete replacement server and exclude preserved versions |
| Obsolete extensions | Explicitly marked in `.obsolete`, not selected by any checked profile, and no remote VS Code process detected |
| Extension installer cache | Unchanged for at least one hour |
| Old logs | Unchanged for at least seven days, including descendants |

Coursework, downloads, personal files, SSH configuration, extension settings, and `User/History` are outside the cleanup scope. Selected items are deleted directly. Development components and installers can be downloaded again; removed logs cannot be recovered through this app.

Paths, ownership, file metadata, profiles, processes, and locks are rechecked before deletion. Changed selections, symbolic-link entry points, NFS busy files, mount points, uncertain process visibility, and incomplete version/profile metadata are preserved or skipped.

## Limits

- CSE home directories can be shared across hosts. The app checks the connected host's processes and shared locks, but cannot enumerate every process on other hosts. Close other remote VS Code connections first.
- Quota limits come from the server; no fixed personal quota is built in. Reclaimable sizes are estimates.
- Personal files filling the quota are listed for inspection, never automatically deleted.
- A disconnected operation may have cleaned some items. Reconnect and scan again to verify the result.
- Unsupported future VS Code layouts are preserved. The app does not fix every connection, password, or network issue.

## Build and test

Use Python 3.14 on Windows to build. The remote engine uses Python 3.9+ standard libraries only.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-lock.txt
.\.venv\Scripts\python.exe -m unittest discover -v
.\build.ps1 -Language both
```

Both editions share the same cleanup code. `tools/localize.py` translates display string constants from `translations/en.json` at build time and fails if a translation is missing. Generated sources and executables go under `build` and `dist`. Personal reports and validation data are excluded from Git and release assets.

Tests cover preservation rules, stale selections, changed profiles, path traversal, symbolic links, NFS busy files, locks, unknown process visibility, and repeated cleanup. Some filesystem tests require Linux or Windows symbolic-link permission.

## References

The implementation is independent. Initial research included [XDflight's CSE cleanup script](https://gist.github.com/XDflight/5f3509eb84fc282b88059c909036f5bc), [remote-ide-server-cleanup](https://github.com/TioSisai/remote-ide-server-cleanup), and [cleanup-vscode-ext.sh](https://gist.github.com/akkuman/c1d9577fab2a8701142887de5dc04270). This app uses explicit metadata and profile checks instead of indiscriminately clearing caches or terminating processes.

See [CSE disk quotas](https://taggi.cse.unsw.edu.au/FAQ/Disk_Quota/) and [VS Code Remote SSH](https://code.visualstudio.com/docs/remote/ssh).
