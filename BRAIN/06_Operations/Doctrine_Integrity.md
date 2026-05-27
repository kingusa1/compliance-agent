---
created: 2026-05-24
updated: 2026-05-27 11:40 UTC
tags: [operations, doctrine, integrity, tamper-evident]
---

# Doctrine Integrity — manifest + changelog

> Records SHA-256 hashes of every binding doctrine file. The pre-push
> hook calls `scripts/doctrine/integrity.py verify`; if any file has
> changed without a matching changelog row, the push is blocked.
>
> To legitimately edit a doctrine file: change it, run
> `python scripts/doctrine/integrity.py bless --reason "<why>"`, commit
> the integrity manifest in the same commit as the edit. The bless
> reason is the audit trail.
>
> Tamper-evidence: `verify` cross-checks the working-tree manifest
> against HEAD's committed manifest. Editing both the LAW and the
> manifest in the working tree (to fake hashes) without an intermediate
> commit fails the cross-check.

<!-- MANIFEST-BEGIN -->
| File | SHA-256 |
|---|---|
| BRAIN/00_LAW_OF_SKILLS.md | `83503df2baf418a95b506c940fa395902e2fbe498f6f750c14b5ac7193f82a87` |
| BRAIN/00_LAW_OF_ENTERPRISE_GRADE.md | `dc1639cfa591cbaa8aef536ab70ea9e7e0a3e709f2bd8bebde4b7f8e60eacbba` |
| BRAIN/06_Operations/Skill_Routing.md | `fcc48dd4506c0ecf1dc06c645eccd4883744915690844ca36da8e5c842449820` |
| BRAIN/06_Operations/Session_Self_Audit.md | `dc835ccffe761bd303849f89b44c427f56560a97ae4c67bf7967f8fccfff8b99` |
| CLAUDE.md | `f0b0a4e4dbc8fa4ab94d940ab0a9c900240592c0f1891af6ddd19899eed35330` |
| scripts/doctrine/audit.py | `b03d80fef18a666fb1e20d7ffca3c22231fdc023281ffacea85007e1c75cbd3b` |
| scripts/doctrine/ledger.py | `eb934e4558255fd610ebc88bb572765aaee5d17deed81827dc2e122f4fca4431` |
| scripts/doctrine/metrics.py | `fdf9680bdeed8c60b3fb2bc6e11c30b358c8956c320e7353351c43cb2bec185b` |
| scripts/doctrine/integrity.py | `6f3689ca9d9bcabb172fe07b7afa4c88857ba12727e9feccac71385390455f6a` |
| scripts/doctrine/_ledger_io.py | `fceb071dcadd3fd5d4365eb618188ef32c04fd7df6db3294e14fe7dc84e671f8` |
| .githooks/pre-commit | `d101363c399abbd46087854f88466662c1812f5f2c72b7a480531a84934f4d54` |
| .githooks/pre-push | `b32439df82a5a15e90693fd8fa2e16c22dd9b02b8de7d35b2df35b94145a8096` |
<!-- MANIFEST-END -->

## Changelog

- **2026-05-27 11:40 UTC** — please make sure to do a deep search on the internet before any wave add that in the brain so you will be 100% sure that the fix is enterprice grade confidance level 100%
- **2026-05-26 12:16 UTC** — owner mandate 2026-05-28: push with kingusa1 not it@bbmgroup please
- **2026-05-25 21:17 UTC** — Add the two-bibles + zero-errors + rule-maintenance doctrine to CLAUDE.md top-of-file BINDING DOCTRINE block, per owner mandate 2026-05-25
- **2026-05-24 11:04 UTC** — post-merge: audit.py encoding='utf-8' fix is on main as part of squash 1cf969f; bless to align manifest with main state
- **2026-05-24 08:23 UTC** — fix audit.py UnicodeDecodeError on Windows when diff contains UTF-8 emojis — subprocess.run now uses encoding='utf-8' with errors='replace'
- **2026-05-24 00:52 UTC** — v2.1 hardening: addressed 3 CRIT + 10 HIGH + 7 MED from 3 reviewers; added _ledger_io shared parser, sanitize_cell, non-waivable hard fails, CI doctrine-gate, hooksPath drift check
- **2026-05-24 00:42 UTC** — Address code-reviewer C1 (exit-code propagation via if !), M1 (resolve_python probe + POSIX fallback), H1 (single-line cross-shell ledger CLI), H4 (forbid --no-verify)
- **2026-05-24 00:36 UTC** — v2.1 wiring: scripts referenced in LAW; CLAUDE.md adds session-start bootstrap + ledger CLI usage
- **2026-05-24 00:34 UTC** — initial doctrine baseline 2026-05-24 (LAW v2 + scripts + hooks)
