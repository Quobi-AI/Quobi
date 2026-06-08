# Security Policy

Quobi is built around a privacy promise: your audio and text stay on your machine. We take reports that affect that promise seriously.

## Reporting a vulnerability

Please do not open a public issue for a security problem. Report it privately through GitHub: go to the **Security** tab of this repository and click **Report a vulnerability** (this uses GitHub's private vulnerability reporting, so the details stay between you and the maintainers).

Include:

- a description of the issue and why it matters,
- the steps to reproduce it,
- the version or commit you tested.

We will acknowledge your report, work on a fix, and credit you when it ships unless you would rather stay anonymous.

## What we especially care about

Given what Quobi is, these are the things we treat as serious:

- anything that causes audio, transcripts, or text to leave the device,
- any network call in the recording, transcription, or cleanup path,
- a model or binary being loaded without its SHA-256 being verified first,
- anything that lets a downloaded model or update run code it should not.

## Supported versions

Quobi is pre-1.0 and moving quickly. Security fixes land on the latest release. If you are running an older build, update before reporting.
