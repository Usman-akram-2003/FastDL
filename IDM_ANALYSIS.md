# IDM artifact analysis and FastDL improvements

## Target and scope

The user supplied `C:\Users\Admin\OneDrive\Desktop\idman643build16.exe`.
Windows version metadata identifies it as **Internet Download Manager installer
6.43.16.1**, by Tonec Inc. It is an x86 Windows installer, not the unpacked
download-engine executable.

SHA-256: `276aa7e4ff7682445b011ebe6ac3920e7eca6ece7d3f6723b1f1cb515164461b`.

The investigation used REA's artifact and JavaScript analysis tools, plus PE
metadata and bounded parsing of embedded ZIP local entries. The installer and
the recovered IDM scripts were not executed. No IDM code or visual assets were
added to FastDL.

## Evidence and limitations

| Evidence | Observation | Boundary |
|---|---|---|
| `ev_5dc195544e6397e1d86fec975bce3fed975d0618ef6c438649308c5e71fbd00f` | REA identified the supplied PE artifact and its SHA-256. | Its generic artifact graph did not unpack the installer payload. |
| `ev_e2af3996bf4e465190a55e756f647a70090ec05e2996a4dd0044db56f3ffe83b` | REA inventory contains the root artifact. | A one-node inventory is not evidence that the installer contains no bundled files. |
| `build/rea-idm/inventory.json` | Custom static extraction found 15 candidate ZIP local entries; five entries passed decompression, declared-size, and CRC checks. | Ten candidates could not be verified/extracted. This is partial recovery, not a complete installer extraction. |
| `ev_5a43d81aadac2807316a07c7d43a7fabc487fb7449ddfe6bb56406cdb55f5ee0` | REA analyzed the five verified files as inert JavaScript/HTML and produced a static application graph. | Dynamic/obfuscated relationships remain unresolved; graph labels do not establish that IDM is an Electron application. |

The recovered browser `content.js`, at installer offset **9641844**, has SHA-256
`0f5e586801349c1b071095c09c02f5ae5920748567532d180d761db9e2b467c1`.
Its relevant static behavior includes:

- `Aa` collects and deduplicates anchor and image-map links, optionally limits
  collection to a selection, and also considers URLs in selected text.
- `gb` and the runtime message dispatcher send collected information to the
  extension's other components. The corresponding background scripts were not
  successfully recovered, so their complete handoff behavior is unknown.
- Mutation, resize, and intersection observers track media elements and changing
  page layout. These establish browser-side tracking machinery, not the time
  required to extract qualities on any particular site.
- Runtime ports and message handlers connect page observations to browser
  extension components. They do not reveal the native downloader's protocol or
  transfer algorithm.

REA doctor reported **no configured native analysis engine**. The cached REA
4.0.1 CLI supplied the static evidence above and matches the installed skill
catalog. An additional check of the latest REA 6.8.0 also reported a Node-version
mismatch and stale/missing integrations. No setup/configuration changes were
made. Native engine decompilation, runtime observation of IDM itself, scheduler
internals, connection allocation, and speed comparisons remain unverified.

## Features mapped to FastDL

FastDL already has dynamic segmented transfers, pause/resume, video quality
selection, categories, batch URL entry, scheduling, speed limits, proxy support,
per-download progress windows, and a site grabber. Those statements describe
FastDL's source; this investigation did not prove equivalence with IDM's native
implementation.

The following improvements were independently implemented:

| Improvement | Basis | FastDL behavior |
|---|---|---|
| Download page links | Recovered IDM browser link-collection behavior | New browser context-menu action collects HTTP(S) anchor/image-map URLs and opens the existing editable batch dialog. |
| Download selected links | Recovered IDM selection-aware collection | Collects selected anchors and URLs from selected text in the clicked frame. |
| Safe batch review | FastDL integration design | Deduplicates links, limits each import to 500 URLs, validates input before UI actions, and starts nothing until the user submits the dialog. |
| Working automatic browser handoff | A reproduced FastDL defect found during comparison | A filename suffix no longer shadows the browser API; declined handoffs resume the browser's download. |
| Range-response integrity | Independent FastDL reliability improvement | Each ranged worker checks the returned byte interval and total before writing, and caps writes to the requested interval. This is not a recovered IDM algorithm. |

Native batch imports reveal the main FastDL window and prefill its existing
*Add many links* dialog. Browser-only fallback uses a one-use, random token with
a five-minute lifetime and a maximum of 20 pending imports. Page links are
reviewed locally; the import does not copy account cookies to other hosts.
An import received while a batch review is open waits for that review to close,
preserving the user's edited links and chosen folder.

Recovered third-party files and machine-readable analysis artifacts remain in
the ignored `build/rea-idm/` directory, separate from application source and
release contents.

## Verification commands

```powershell
node test_extension.cjs
node test_button.cjs
node test_browser_downloads.cjs
python -m unittest test_batch_import test_range_responses test_quality_lookup -v
python test_fastdl.py
```

The focused checks cover selection/frame handoff, deduplication and unsafe-scheme
filtering, user review before queueing, native/browser import paths, one-use
tokens, API trust checks, declined browser handoffs, valid ranges, wrong range
offset/end/total, malformed/missing range metadata, and empty truncated replies.
They do not establish native IDM equivalence, relative throughput, or complete
resumable-content identity. ETag-based same-length file-change detection and
mirror content identity remain separate follow-up work.

### Observed results

- The existing full Python regression script completed with `all good` and exit
  status 0. Its simulated antivirus and expired-page diagnostics were emitted.
- Eight focused Python tests passed, including parameterized rejection cases.
- The Node test runner passed three test files covering 22 named extension
  scenarios, with no failures.
- A real isolated Brave browser exercised the content script's actual page and
  selection extraction, background-to-API handoff, one-use browser review,
  preserved edits/folder, and queued next review. The download queue remained
  empty until user submission. Screenshot: `build/batch-review-browser.png`.
- The packaged FastDL 1.13 executable downloaded a 3,000,017-byte payload from a
  local range server, and its saved file matched the input exactly.
- The installer build completed. Its SHA-256 matched `dist/latest.json`; the
  bundled `ui.html` matched the source by SHA-256.
- The new Python and extension regression checks were added to the release
  workflow. The remote workflow itself was not run in this session.

## Investigation checklist

| Question | Status |
|---|---|
| Identify the supplied target and fingerprint it | Answered |
| Recover and inspect relevant shipped browser behavior | Partially answered: five verified files; remaining payload not recovered |
| Establish IDM's native core transfer implementation | Unresolved: no configured native analysis provider |
| Compare recovered capabilities with existing FastDL features | Answered within the documented browser/source boundary |
| Implement the selected missing browser capabilities independently | Complete |
| Verify the selected changes and produce an installer | Complete; native tray-window operation remains mocked rather than observed |
