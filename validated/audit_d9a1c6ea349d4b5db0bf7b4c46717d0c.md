### Title
Path traversal escape via glob pattern containing `..` segments after `SplitPattern` reintroduces out-of-tree path - ([File: commands/helpers/file_archiver.go])

### Summary
`findRelativePathInProject` validates only the `base` portion of a glob path against directory-traversal via `filepath.Rel`/`..` prefix check, but the `patt` portion returned by `doublestar.SplitPattern` is never validated. Because `filepath.Join(rel, patt)` invokes `filepath.Clean`, which treats literal wildcard segments (e.g. `*`) as ordinary path components, an attacker-supplied pattern such as `subdir/*/../../../etc/passwd` can have its wildcard segment "cancelled out" by subsequent `..` components during `Clean`, producing a final relative path (e.g. `../etc/passwd`) that escapes the project working directory even though the `base`-only check passed.

### Finding Description
`processPath` (commands/helpers/file_archiver.go:146-164) calls `findRelativePathInProject(path)` (lines 191-222) and then globs the returned string with `doublestar.FilepathGlob(rel, ...)`. Inside `findRelativePathInProject`:
- If the raw path contains glob metacharacters, `doublestar.SplitPattern(slashPath)` splits it into a `base` (literal prefix, no meta chars) and `patt` (remainder, which may still contain literal `..` segments interleaved with wildcard segments) — lines 199-201.
- Only `base` is resolved with `filepath.Abs` and checked against `c.wd` via `filepath.Rel` plus the `".."` prefix test (lines 203-216).
- The unchecked `patt` is then appended back with `rel = filepath.Join(rel, patt)` (line 219).

`filepath.Join`/`Clean` operate purely lexically: they don't understand glob semantics and treat a wildcard segment like `*` as an opaque literal directory name. So a pattern such as `subdir/*/../../../etc/passwd`, given a `c.wd` of `/builds/project`:
- `base` = `subdir`, `patt` = `*/../../../etc/passwd`.
- `Abs(base)` = `/builds/project/subdir`, which is inside `c.wd`, so `rel = "subdir"` passes the `..` prefix check (the guard the code relies on).
- `filepath.Join("subdir", "*/../../../etc/passwd")` lexically collapses `subdir/*` against the following `..`/`..`, ultimately underflowing the relative stack and yielding `../etc/passwd` — a path outside the project root, silently reconstructed *after* the only containment check in the function.

This final string is passed unchecked to `doublestar.FilepathGlob`, which then expands the glob from the working directory, effectively globbing `../etc/passwd` relative to `c.wd`, exactly matching the escape pattern the question describes — the check happens on `base` only and `patt` reintroduces the escape.

Existing protections are insufficient: the only guard is the `..`-prefix check on `rel` derived from `base`, executed *before* `patt` is joined back in. There is no re-validation of the final joined `rel` string, and no rejection of `..` components appearing inside `patt`.

### Impact Explanation
A pipeline author who controls `artifacts:paths` (or `artifacts:exclude`, since `isExcluded` calls the same function at commands/helpers/file_archiver.go:108) can craft an entry whose glob "pattern" segment contains `..` components positioned after a wildcard segment, causing the resolved glob root to fall outside the job's working directory tree. `FilepathGlob` will then enumerate/match files outside the intended build directory, and matched files are subsequently walked and archived by `process`/`add` (lines 65-101, 127-138), which only re-validates via the same `filepath.Rel`/`..`-prefix logic in `process` itself for the *matched paths* — but the glob has already been permitted to search outside `c.wd`, undermining the containment invariant at the point the search space is established. This is a boundary/logic bug regardless of whether `process`'s own secondary check (applied to matched files, not directories) fully prevents inclusion in every case, because the design intent that "artifact path resolution must stay within `c.wd`" is violated at the glob-root stage.

### Likelihood Explanation
The precondition is simply an unprivileged pipeline author who can set `artifacts:paths`/`artifacts:exclude` in `.gitlab-ci.yml` — a completely standard, always-available capability. No special runner configuration or privileged access is required. Constructing the specific literal-segment/`..`/wildcard combination that collapses via `filepath.Clean` requires precise crafting but is deterministic and fully reproducible; the mechanics rely only on documented `filepath.Join`/`Clean` semantics.

### Recommendation
After computing `rel = filepath.Join(rel, patt)` in `findRelativePathInProject`, re-validate that the final joined path does not escape `c.wd` (perform the same `Abs`+`Rel`+`".."`-prefix check on the fully joined literal-glob-free path, or reject any `patt` whose literal (non-wildcard) components resolve outside the already-validated `base`). Alternatively, avoid using `filepath.Join`/`Clean` to recombine `base` and `patt`, and instead validate that `doublestar.Clean`/pattern normalization does not allow `..` segments to appear after any wildcard segment in `patt`.

### Proof of Concept
Go unit test targeting `findRelativePathInProject` directly:
```go
func TestFindRelativePathInProject_PatternEscape(t *testing.T) {
    c := &fileArchiver{wd: "/builds/project"}
    rel, err := c.findRelativePathInProject("subdir/*/../../../etc/passwd")
    // Expect: escaping pattern must be rejected
    assert.Error(t, err)
    assert.NotContains(t, rel, "..")
}
```
Expected current (buggy) behavior: `err == nil` and `rel == "../etc/passwd"` (or similar), demonstrating the joined path escapes `c.wd` despite the base-only containment check passing. After the fix, this call should return a non-nil error (`"artifact path is not a subpath of project directory"`).