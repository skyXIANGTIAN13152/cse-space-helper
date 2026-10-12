# Releases

## 1.2.0

- Ask before updating when a newer stable release is found at startup or during a six-hour background check.
- Download the matching language edition with progress and cancellation; verify its size and GitHub SHA-256 digest.
- Replace the existing executable in place and restart through a temporary helper, preserving shortcuts.
- Defer prompts until scanning or cleanup finishes; restore the previous executable if installation or startup fails.
- Keep the warm outline interface and transparent amber icon in both language editions.

## 1.1.0

- Check the public GitHub release at startup without blocking scans or cleanup.
- Add a manual update check and a link to the verified release page.
- Keep update checks separate from CSE connections; no account or report data is sent.
- Validate version numbers, repository URLs, and the matching language asset; handle offline, limited, incomplete, and invalid responses.
- Normalize temporary paths in Windows CI test fixtures, including short-path aliases.

## 1.0.0

- First public Chinese and fully English Windows x64 releases.
- Review CSE quota and selected old VS Code components before cleaning.
- Preserve coursework, personal files, current/latest/running servers, and profile-selected extensions.
- Use the yellow shield and blue terminal app icon.
