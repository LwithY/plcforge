# Source references

The user supplied `claude-code-main.zip`. Its package metadata identifies
`claude-code-best` version 2.8.4 and repository
https://github.com/claude-code-best/claude-code . The README describes it as a
reverse-engineered restoration of Claude Code, with educational/research wording.
No top-level LICENSE file was present in the supplied archive; package.json did
not declare a license field. This record does not assign a license to upstream.

Selected unmodified files are retained only in the developer's local reference
archive. They are not included in the public repository or npm package.
The executable PLCForge Python modules were written for this task by following
the control-flow concepts; no upstream function was directly pasted into the
runtime. The MIT license covers PLCForge's own code, not upstream material.

PLCForge does not represent an official Anthropic product. Names and source
claims remain attributed to their respective authors. The clean source package
omits the archived upstream files. The npm installer downloads Python only from
the official Python website, verifies its pinned SHA-256, and preserves Python's
bundled license files in the user's runtime directory. Python is not bundled in
the npm tarball. Its license is available at https://docs.python.org/3/license.html .
