# Third-party notices

Naming routines and the legacy absolute-callback compatibility originated from
lowestprime/codex-exporter 0.3.12, commit
`8db0172cab2d3db9d257b252062ebc1f8fe0f3f0`. The current stage renderer and archive
engine use the newer pinned implementation described below.

The recorded timestamp formatter, listing sorter/pager and interactive browser
are also adapted from the pinned v0.5.2 source. Their Antigravity changes retain
qualified storage identities, current project metadata and native step anchors.

The archive, compression planning, publication/access, diagnostic, and progress
engine is adapted from lowestprime/codex-exporter v0.5.2, commit
`acd322d1987f1078b6f79dcba62c898704dcc5ba`. It is vendored under
`antigravity_exporter/_vendor/codex_archive`; it is not downloaded at runtime.
`PROVENANCE.json` records upstream and adapted SHA-256 for each retained file.
Changes support scoped nested Antigravity native members, Antigravity metadata
and access-repair names, existing absolute progress callbacks, and timeouts.
The Codex-specific transcript packaging layer is excluded. No Codex parser or
private source data is bundled into this archive engine.

`tools/ci_7zip.py` is adapted from the same commit; it downloads hash-pinned
**official 7-Zip** test binaries for CI only. 7-Zip is not redistributed in this
package. See https://www.7-zip.org/license.txt for its license terms.

Upstream license, retained in full:

```text
MIT License

Copyright (c) 2026 Cooper Beaman

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```
