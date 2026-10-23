<div align="center">

# Mac Cleanup Assistant

**Look first. Then act.** A macOS disk cleaner that scans read-only, grades every item in three colours, lets you tick individual rows, and moves files to the Trash instead of deleting them.

[![macOS 13+](https://img.shields.io/badge/macOS-13%2B-000000?style=flat-square&logo=apple&logoColor=white)](#install) [![Swift + Python](https://img.shields.io/badge/Swift%20%2B%20Python-3.9%2B-F05138?style=flat-square&logo=swift&logoColor=white)](#how-it-works) [![Local only](https://img.shields.io/badge/network-127.0.0.1%20only-6FF7C8?style=flat-square)](#privacy) [![No dependencies](https://img.shields.io/badge/front--end-zero%20dependencies-3FA66A?style=flat-square)](#how-it-works)

[简体中文](README.md) · **English**

<img src="docs/assets/hero.jpg" alt="Mac Cleanup Assistant: disk drum, three-colour list and cleanup actions" width="100%">

<sub>Main window · the drum shows disk usage and item size; the list is grouped into safe / needs your call / system-managed</sub>

</div>

---

## Why this exists

When a Mac's disk fills up, most cleaners ask you to trust a black box: press "optimise" and accept a released-space number with no provenance.

Mac Cleanup Assistant does the opposite: **lay out where every byte comes from, then let you decide.**

- **Read-only scan** — nothing is touched until you explicitly confirm.
- **Three-colour grading** — green is pure cache or safely rebuildable, yellow contains user data or is uncertain, red should be left alone.
- **Selection only narrows** — ticking rows can only shrink the allow-list; it never expands beyond the safety boundary.
- **Reversible by default** — cleanup moves files to the Trash, not `rm -rf`. Emptying the Trash is a separate, explicit action.
- **Literal honesty** — the "retired" stamp is only applied when the server returns `ok:true`; if everything is skipped, it says the disk did not change.

## Features

| Capability | What it does |
|---|---|
| **Read-only scan** | Caches, logs, build artefacts, uninstall leftovers, installers and large files; usually 1–3 minutes |
| **Size drum** | Rim level = disk usage; each card on the drum = one item, card height = size (log scale); drag to rotate, cards pull out when ticked |
| **Three drawers** | Safe to clean / needs your call / system-managed, with size ticks, path and an explanation per card |
| **Per-item selection** | Tick one card or use quick picks: all, none, ≥100 MB, cache only, invert |
| **Full report** | A static HTML report with disk overview, memory / load diagnosis, three-colour list, background services and large files |
| **Honest receipt** | Reports only the items actually processed and the real space change; failures explain why and never get a success stamp |
| **AI review (optional)** | Directories the rule table cannot classify can be sent to TypeSafe Jev for a typed verdict with delete probability, recoverability and confidence |
| **Protection list and history** | Protected paths are never cleaned; every operation is logged |
| **Three shells** | Native app window (WKWebView), a web-shell `.app`, and a `--classic` AppKit control interface |

<a href="docs/assets/drum.jpg"><img src="docs/assets/drum.jpg" alt="Close-up of the size drum" width="420" align="right"></a>

### Interface: an archive room

The visual language borrows from an archive: type hierarchy, restrained colour, dense information and one signature action — cards dissolve into dust and spiral into the drum while free space ticks up.

Motion only signals state and causality; it never covers content. Touch targets stay at least 44 px on narrow screens, long paths wrap safely, and `prefers-reduced-motion` stops particles and entrance animations while keeping state feedback.

## Safety model

Three layers come from the cleanup engine, two from the app:

1. **Allow-list** — only paths the report marked cleanable are processed.
2. **Hard protection** — system directories, HOME and its immediate children, credentials (`~/.ssh`, Keychains, Mail, Safari), backup and security software, VM / container data and AI model caches are refused even if the rule table says green.
3. **User protection** — paths in `~/.config/macos-deep-optimize/whitelist` are skipped.
4. **Preview by default** — no preview, no action.
5. **Confirmation text** — execution requires typing the confirmation phrase; the backend validates it too, so calling the API directly is rejected as well.

There is also a **rebuildability gate**: a build artefact is only marked safely rebuildable when a lock file (26 kinds) exists at the project root found by walking upwards, it was not modified today, and the git tree has no uncommitted changes.

Logs and crash reports are kept for 7 days by default and only older ones are removed — they are diagnostic evidence, not junk.

## How it works

```text
WKWebView / browser
        │  HTTP (127.0.0.1 only)
        ▼
app/server.py            local HTTP shell · state management · no cleanup rules
        │
        ├─ scan.py          read-only scan · size and rebuildability checks
        ├─ report.py        three-colour grading · static HTML report
        ├─ clean.py         allow-list · hard protection · move to Trash
        └─ judge.py         optional TypeSafe Jev review
```

- The interface is a **single HTML file** with zero third-party JS/CSS dependencies and works fully offline.
- The native shell is Swift + WKWebView; the engine is Python, taken from the `macos-deep-optimize` skill. `app/skill-scripts/` in this repo is the authoritative copy.
- The only outbound request is the opt-in AI review: directory path, size, last-modified time and subdirectory names — never file contents.

## Install

Requirements: macOS 13+, Xcode Command Line Tools (for `swiftc` and `python3`).

```bash
git clone https://github.com/baiiiii521/mac-cleanup-app.git
cd mac-cleanup-app

# Native app → ~/Applications/Mac 清理助手.app
bash native/build_native.sh

# Or the web-shell app (interface in your browser)
bash build.sh
```

The build signs the app ad-hoc. If Gatekeeper blocks the first launch, right-click the icon → Open, or run:

```bash
xattr -dr com.apple.quarantine ~/Applications/Mac\ 清理助手.app
```

## Usage

1. **Run a scan** — read-only; free space is shown live at the top.
2. **Read the report** — three-colour list, background services and large files; open the full HTML report in a new tab if you prefer.
3. **Tick items** — green is selected by default, yellow only allows pure cache subdirectories, red and AI-unreviewed items cannot be auto-cleaned.
4. **Preview, then execute** — the preview lists only ticked items; the receipt shows how many moved to the Trash and how free space changed. To actually reclaim the space, empty the Trash separately (confirmation phrase required).

### Self-test

Verify the whole path without clicking:

```bash
"$HOME/Applications/Mac 清理助手.app/Contents/MacOS/MacCleanup" --selftest
```

It uses the same code path as the buttons: engine readiness, item count, AI results merged, two ticked items produce exactly two actions, a wrong confirmation phrase is rejected, an empty selection is rejected — 10 checks in total.

## Project layout

```text
mac-cleanup-app/
├── app/
│   ├── launch                .app entry: pick Python → start service → open UI
│   ├── server.py             local HTTP shell and state
│   ├── skill-scripts/        cleanup engine (authoritative copy, tracked)
│   ├── index.html            interface (single file, no external assets)
│   └── Info.plist            app metadata
├── native/                   Swift + WKWebView native shell
├── tools/                    icon, screenshot and interaction scripts
├── docs/mole-notes/          research notes on cleanup rules
├── .impeccable/              design review screenshots and decisions
└── build.sh                  web-shell packaging
```

Runtime state and logs:

```text
~/.local/state/mac-cleanup-app/                    scan.json / report.html / port / server.log
~/.local/state/macos-deep-optimize/operations.log  cleanup operation log
```

## Measurements

Re-measured on the author's Mac on 2026-10-18, after P7:

- Disk 228.3 GB total, 152.3 GB used, 47.2 GB free (67% full).
- 🟢 9 items / 4.65 GB safe to clean; 🟡 42 items / 29.3 GB need a decision; 🔴 1 item.
- Scan time **53.2 s**, down from 86.7 s (−39%).
- Detected 4 sets of uninstall leftovers, 12 rebuildable artefacts and 2 leftover installers.

These numbers come from a real scan, not demo data.

## Limitations

- macOS only; relies on the system `python3` (run `xcode-select --install` if missing).
- The app is ad-hoc signed and not notarised; the first launch needs a manual approval.
- Cleanup goes to the Trash and does not free space immediately; emptying the Trash is irreversible and separately confirmed.
- AI review needs a TypeSafe key (`TYPESAFE_API_KEY` or `~/.config/typesafe/key`) and sends directory metadata out; everything else works without it.
- The green list is deliberately conservative — fewer items cleaned rather than a wrong deletion.
- No automated test suite; after changes run `--selftest` and `node tools/verify-interaction.js`.

## Acknowledgements

- Cleanup rules and the safety model come from the `macos-deep-optimize` skill
- `docs/mole-notes/` draws on [mole](https://github.com/tw93/Mole)'s rules and protection lists
- AI review is provided by TypeSafe Jev

## License

No open-source license is declared. Contact the author before reusing code or rules.
