### Title
Unvalidated symlink target (`hdr.Linkname`) allows workspace escape via crafted tar+zstd archive - ([File: commands/helpers/archive/tarzstd/tarzstd_extractor.go])

### Summary
`extractor.Extract` validates only `hdr.Name` against the extraction root `e.dir` using `filepath.Abs`/`strings.HasPrefix`, but never validates `hdr.Linkname` before calling `os.Symlink(hdr.Linkname, path)` for `tar.TypeSymlink` entries. A pipeline author who controls a cache/artifact archive can therefore create a symlink that physically resides inside the workspace (at a chroot-checked `path`) but points to an arbitrary absolute path outside it.

### Finding Description
The chroot check at [1](#0-0)  only bounds where the symlink *file itself* is created (derived from `hdr.Name`). The symlink's *target* comes straight from the archive's `hdr.Linkname` field and is passed unchecked to `os.Symlink` in the deferred-processing loop: [2](#0-1) . Nothing constrains `hdr.Linkname` to stay within `e.dir` — it can be an absolute Unix path, a Windows drive-absolute path, or a UNC path. This is attacker-reachable because cache/artifact archives are read directly from user/pipeline-controlled uploads (job artifacts, cache) and processed by this extractor as part of normal job execution.

### Impact Explanation
Once extracted, the workspace contains a symlink at a path the job (or generated build/shell scripts) will subsequently reference as if it were an ordinary in-workspace file. Any later read/write/execute through that workspace-relative path is redirected by the OS to the attacker-chosen absolute target, giving unauthorized file read/write outside the job root, and — if a generated script writes and then executes through that path — command execution outside the intended workspace on the runner host/container filesystem.

### Likelihood Explanation
This requires only the ability to submit a job whose cache or artifacts are populated with a custom-crafted tar+zstd archive (a normal, unprivileged CI capability — e.g., pushing an artifact from an earlier stage/job or supplying a pre-populated cache). No special runner configuration or privilege is needed, and the extraction path is exercised on every cache/artifact restore. Comparable zip-based extractors (`helpers/archives/zip_extract.go`, `extractZipSymlinkEntry`) exhibit the same missing-validation pattern, indicating this isn't a one-off oversight but a systemic gap in symlink-target validation across the archive extractors.

### Recommendation
Validate `hdr.Linkname` the same way `hdr.Name` is validated: resolve it relative to `filepath.Dir(path)` (matching tar symlink semantics) via `filepath.Abs`/`filepath.Clean`, and reject (or rewrite/skip) entries whose resolved target escapes `e.dir`. Apply the same fix to other extractors that create symlinks from archive-provided targets (e.g. `zip_extract.go`).

### Proof of Concept
Go unit test for `commands/helpers/archive/tarzstd/tarzstd_extractor.go`:
1. Build an in-memory tar+zstd stream containing a single `tar.TypeSymlink` header with `Name: "innocuous-link"` and `Linkname: "/etc/passwd"` (or `` `C:\Windows\System32\evil.ps1` `` on Windows).
2. Call `NewExtractor(reader, size, dir).Extract(ctx)` with `dir` set to a temp directory.
3. Assert `Extract` returns no error (demonstrating no validation blocks it).
4. Call `os.Lstat(filepath.Join(dir, "innocuous-link"))`, confirm it's a symlink, then `os.Readlink` and assert the target equals `/etc/passwd`, i.e., it resolves outside `dir`.
5. Optionally, write through `filepath.Join(dir, "innocuous-link")` and confirm the write lands at `/etc/passwd` (or the chosen target), proving the escape.

### Citations

**File:** commands/helpers/archive/tarzstd/tarzstd_extractor.go (L57-64)
```go
		var path string
		path, err = filepath.Abs(filepath.Join(e.dir, hdr.Name))
		if err != nil {
			return err
		}
		if !strings.HasPrefix(path, e.dir+string(filepath.Separator)) && path != e.dir {
			return fmt.Errorf("%s cannot be extracted outside of chroot (%s)", path, e.dir)
		}
```

**File:** commands/helpers/archive/tarzstd/tarzstd_extractor.go (L107-117)
```go
	for path, hdr := range deferred {
		fi := hdr.FileInfo()
		if fi.Mode()&os.ModeSymlink == 0 && !fi.Mode().IsDir() {
			continue
		}

		if fi.Mode()&os.ModeSymlink != 0 {
			if err := os.Symlink(hdr.Linkname, path); err != nil {
				return err
			}
		}
```
