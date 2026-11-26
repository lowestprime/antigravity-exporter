# Catalog and storage resolution

## The failure addressed

Antigravity can keep a conversation's searchable summary in one application
variant and its transcript in another. A SQLite summary row's `app_data_dir`
identifies the storage variant. Treating the location of
`conversation_summaries.db` as the location of every row's transcript creates
false duplicate UUIDs and can send project exports to nonexistent log paths.

The exporter now treats `(storage variant, conversation UUID)` as the identity.
It reads an explicitly recorded `app_data_dir` when present, accepts only the
known local variant names, and merges metadata records only for that exact
identity. It records the original catalog paths and the routing rule. It does
not search arbitrary directories mentioned by a summary row.

When `app_data_dir` is absent or empty, the catalog's variant remains the storage
variant. Real instances of the same UUID in distinct storage variants remain
separate. An exact UUID is not sufficient to choose between genuinely distinct
sources. Select one explicitly:

```powershell
agy-thread 'antigravity:11111111-2222-4333-8444-555555555555'
agy-thread '11111111-2222-4333-8444-555555555555' --variant antigravity
```

Selecting `--root` at a specific variant directory keeps discovery scoped to
that variant. A qualified selector cannot override a conflicting `--variant`.

## Hub summary metadata

Some newer stores have `agyhub_summaries_proto.pb` instead of a main SQLite
summary catalog. The exporter includes a bounded adapter for the observed
summary map layout. `jetbox_summaries_proto.pb` is supported when it uses the
same layout. The adapter requires UUID map keys, complete protobuf wire fields,
expected scalar types, valid UTC timestamp ranges, and valid recorded parent
UUIDs. It extracts catalog metadata only: title, recorded step count, modified
time, project ID, workspace URIs, and recorded agent/parent identifiers.

This is an observed-layout adapter, not an official Google schema and not a
trajectory decoder. It does not turn arbitrary protobuf text fragments into
conversation events. Unknown wire fields remain uninterpreted. Each adapted
entry reports the source SHA-256, entry SHA-256, source path, and field-number
inventory. Raw global catalog contents are not embedded in session objects or
shared exports; a global catalog may describe unrelated conversations. Original
bytes remain in the selected app data or input archive.

Metadata is captured with the same verified source-copy machinery as other
non-log sources before parsing. A live metadata replacement, truncation or
observed rewrite during capture causes a retryable error. Captured metadata is
reused during that operation. A SQLite catalog is read from a backup that includes
its committed WAL state.

If two metadata records for the same storage identity differ, the most recent
recorded timestamp takes precedence. Equal timestamps prefer the source's hub
catalog, then a stable source path. Recorded conflicting values and that rule
remain in `metadata_conflicts`. An explicit annotation title still determines
the display title. Project membership comes from a recorded project ID matched
to the local project configuration, not from a guessed folder name.

## Availability and chunk reconstruction

Session objects report `source_availability`, including `full`, `full-chunks`,
`invalid-full-chunks`, `reduced-only`, and `catalog-only`. An empty chunk directory
does not count as a transcript. Metadata-only entries remain visible for audit;
they do not establish that transcript content can be recovered.

Full chunks are byte slices, so the exporter concatenates them without inserting
newlines or attempting to parse each slice. It sorts their filenames numerically
and requires contiguous numbers beginning at zero. Unpadded names such as
`0.jsonl`, `1.jsonl`, and `10.jsonl` are handled correctly. Gaps, duplicate numeric
names, and unexpected nested names are rejected. Captures of the same UUID in
different variants or repeated captures use distinct staging paths.

A reduced transcript still requires `--allow-reduced`. Catalog resolution never
silently changes this fidelity choice and never declares missing source content
complete.

## Direct compressed input

`--jsonl` accepts a plain `.jsonl`, `.jsonl.gz`, `.jsonl.zst`, or `.jsonl.zstd`
file. Gzip uses Python's standard library. Zstandard requires the optional
`zstandard` package in the same Python environment as the exporter.

The exporter captures a fixed physical prefix and verifies its SHA-256 before
decoding. Gzip members are decoded with their CRC and end-of-member checks.
Zstandard framing is scanned for complete headers, blocks and optional checksum
bytes, and then decoded once through the native streaming reader; native content
checksums are verified when present. Concatenated compression members/frames are
supported. Incomplete captured compressed frames fail instead of producing a
successful partial transcript. JSONL event selection occurs on the verified
decoded file. The manifest distinguishes the compressed source hash/size from
the decoded JSONL hash/size. Native JSONL output means those decoded source bytes;
it is not falsely described as a byte-identical copy of the compressed file.

Python output buffers remain bounded during decoding. The Zstandard library
also uses its native decoding window, which depends on the input frame. Compressed
input is never read wholesale into a Python byte string. No Python dependency is
downloaded implicitly and no source file is rewritten or recompressed.
