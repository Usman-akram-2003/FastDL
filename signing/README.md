# Code signing with SignPath (free for open source)

`.github/workflows/release.yml` builds FastDL on GitHub and publishes the release. It builds **unsigned** until SignPath
is set up; then it signs the installer by itself. Nothing in the repo needs to change when that happens.

## One-time setup (after SignPath Foundation approves the project)

1. Apply at https://signpath.org/apply (project page, license MIT, repository link).
2. In SignPath, create:
   - **Project** with slug `fastdl`
   - **Artifact configuration** with slug `installer`: paste `signing/artifact-configuration.xml`
   - **Signing policy** with slug `release-signing` (the Foundation certificate), with yourself as approver
   - Add the predefined **GitHub.com trusted build system** to the organization and link it to the project
   - Install the **SignPath GitHub App** on this repository
   - A **user API token** for a submitter user
3. In this repository's Settings > Secrets and variables > Actions:
   - secret `SIGNPATH_API_TOKEN` = that token
   - variable `SIGNPATH_ORGANIZATION_ID` = your SignPath organization id

From then on every `v*` tag is signed. The slugs above are the ones the workflow uses; change both places together.

## Releasing

1. Raise `APP_VERSION` in `fastdl.py`, commit, push.
2. `git tag vX.Y && git push origin vX.Y`. The workflow runs the tests, builds, signs, and publishes the release.
3. `python manifests.py`, then commit `bucket/` (Scoop), and send the winget pull request.

## What is signed

Only `FastDL-Setup.exe`, the file people download (so Windows' SmartScreen sees a publisher). The program inside it
(`FastDL.exe`) and the uninstaller are not signed yet; signing them too means a second SignPath request before the
installer is built.
