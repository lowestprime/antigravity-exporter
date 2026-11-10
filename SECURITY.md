# Security and privacy

Conversation exports can include credentials, personal data, filesystem paths,
source code, tool output, and historical prompts. Treat them as confidential even
when their filenames omit PRIVATE. Credential redaction is best effort, not a
permission to publish the transcript. Do not attach real archives to public issues.

The exporter reads application-owned data and never submits a prompt. It does not
execute commands found in transcripts or load untrusted database extensions.
Output and input are separated. On failure, recoverable private staged copies may
remain; the printed path identifies them. Remove those exporter-owned copies when
no longer needed, not the original application profile.

Only explicitly installed archiver executables run, with argument arrays and no
shell interpolation. Do not configure an executable from an untrusted download.
ZIP member traversal, symlink members, duplicate names, and encrypted members are
rejected. Full decoding can consume significant memory for LZMA dictionaries;
bounded read buffers are not a total process-memory guarantee.

For a suspected vulnerability, use a minimal synthetic reproduction. Use GitHub's
private vulnerability reporting when enabled, rather than public credentials or
real data. The repository owner must explicitly enable that GitHub setting; a
policy document does not enable it. No security-response SLA is promised.

The public allowlist and scanner reduce accidental publication risk but are not a
complete secret detector. Maintainers must review every staged change and the
distribution inventory before refreshing the allowlist or making a release.
