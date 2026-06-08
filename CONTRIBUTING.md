# Contributing to Quobi

Thanks for taking a look. Quobi is a privacy-first dictation app, and contributions that keep it fast, local, and easy to trust are very welcome.

## Before you start

- For anything bigger than a small fix, open an issue first so we can agree on the approach before you spend time on it.
- For bugs, include your OS, your GPU (or "no GPU"), and the relevant lines from the log at `~/.local/state/voice-type/voice-type.log`.

## Building

The full build for Linux and Windows is in [BUILD.md](BUILD.md). You will need Rust, Bun, and Python 3, plus the system libraries listed there.

## Ground rules

- Keep the dictation path offline. Nothing about recording, transcription, or cleanup should make a network call. Model downloads are the only network activity, and they are explicit and SHA-256 verified.
- Match the style of the code around you. Comments explain why, not what.
- Test your change on a real run, not just a build. If you touch the daemon, dictate something and confirm it still works.

## Licensing of contributions

Quobi uses a **Contributor License Agreement (CLA)**. Before your first contribution is merged, you sign it once (a CLA bot posts the link on your pull request). The CLA keeps your contribution under the project's **AGPL-3.0** and also gives Quobi the right to offer the project under a commercial license, which is what keeps that option open (see [LICENSING.md](LICENSING.md)). The full text is in [CLA.md](CLA.md).

## Pull requests

- One focused change per pull request.
- Write a clear title and explain what changed and why.
- Make sure the project still builds.
