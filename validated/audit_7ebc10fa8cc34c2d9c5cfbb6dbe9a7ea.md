### Title
Symlink target is never validated during tar+zstd extraction, allowing a later regular-file entry (or a subsequent extraction into the same build/cache directory) to write outside the extraction root - (File: commands/helpers/archive/tarzstd/tarzstd_extractor.go)

### Summary
`extractor.Extract` validates that a tar entry's own nominal path stays inside `e.dir` [1](#0-0) , but it never checks `hdr.Linkname` for symlink entries before creating them with `os.Symlink(hdr.Linkname, path)` [2](#0-1) . Because a regular file's destination path is computed with a plain string `filepath.Join`/`HasPrefix` check that does not resolve intermediate symlinks, a symlink planted at an earlier point (either earlier in the same archive extraction into a directory used across job runs, e.g. cache directory reuse) lets a later regular-file write follow the link out of `e.dir`.

### Finding Description
The relevant check is:
```go
path, err = filepath.Abs(filepath.Join(e.dir, hdr.Name))
...
if !strings.HasPrefix(path, e.dir+string(filepath.Separator)) && path != e.dir {
    return fmt.Errorf("%s cannot be extracted outside of chroot (%s)", path, e.dir)
}
``` [1](#0-0) 
This only validates the *literal string* of the entry's own name joined to `e.dir`; it never inspects `hdr.Linkname`, and it never checks whether any path component already resolves (via `os.Lstat`/`filepath.EvalSymlinks`) to something outside `e.dir`. Symlink entries are deferred and created at the very end via `os.Symlink(hdr.Linkname, path)` with no restriction on the link target [3](#0-2) .

Two exploitable orderings exist:
1. **Cross-extraction reuse**: If a symlink such as `evil -> /etc` is successfully created by one extraction into a build/cache directory that is later reused (e.g., cache restored into the working directory on a subsequent job/pipeline run using the same runner host and persisted directory), a later archive containing a regular file entry named `evil/payload` will pass the lexical prefix check (the string `e.dir/evil/payload` still starts with `e.dir`) but `os.Create(path)` at line 88 will physically resolve through the symlink and write to `/etc/payload`, escaping `e.dir` entirely.
2. **Within-extraction race via `MkdirAll`**: Because symlinks are deferred to the end of the loop, but `os.MkdirAll(filepath.Dir(path), 0777)` is called eagerly at line 66 for *every* entry (including files that will be written before the deferred symlink pass), a within-archive ordering where the parent directory of a later "symlinked" path is pre-created cannot itself escape (MkdirAll only creates directories under the already-validated `path`). So the concrete, reachable escape is the **cross-extraction/persisted-directory** scenario, not a pure single-archive reordering trick, since regular files are written immediately in the first loop (lines 87-99) while symlinks are deferred and only created afterward (lines 107-117) — a symlink created in the same archive extraction cannot be used by a regular-file entry appearing earlier in that same tar stream to escape, because the symlink doesn't exist yet when the file is written. The realistic trigger is a symlink surviving from a prior extraction into a shared/reused directory.

### Impact Explanation
If an attacker's job populates a cache/artifact with a malicious symlink pointing outside the intended extraction root, and that directory is later reused for another extraction (persisted build directory / shared cache path on the same runner host across job runs), a subsequent regular-file entry with the same name as the symlink will be written through it to an attacker-chosen location outside `e.dir`. This is an unauthorized file write outside the job root, matching the scoped impact ("unauthorized file write outside job root / persistent cross-job disruption if cache is shared").

### Likelihood Explanation
This requires: (1) the attacker can get a symlink with an off-root `Linkname` extracted successfully into a directory (trivial — nothing stops symlink target validation), and (2) that same directory is reused by a subsequent extraction without being wiped (plausible with `GIT_STRATEGY=fetch`/persisted builds directory on shell/non-ephemeral executors, or shared cache paths). Precondition (2) depends on runner/executor configuration (directory reuse), which is a realistic and common configuration (not an admin-misconfiguration edge case) rather than a purely theoretical one.

### Recommendation
Validate `hdr.Linkname` the same way `hdr.Name` is validated: resolve the symlink target relative to the containing directory, canonicalize with `filepath.Abs`/`filepath.Clean`, and reject any target that would resolve outside `e.dir`. Additionally, when creating parent directories or writing regular files, use `os.Lstat` on each path component (or `filepath.EvalSymlinks` up to `e.dir`) to detect and reject writes through pre-existing symlinks that point outside the extraction root, rather than relying solely on a literal string prefix check of the un-resolved path.

### Proof of Concept
Go unit test in `tarzstd` package:
1. Extraction A: build a tar+zstd archive containing one entry: symlink `link -> /tmp/outside-<random>` (a directory outside a fresh `t.TempDir()` used as `e.dir`). Call `extractor.Extract` — assert it currently succeeds (no Linkname validation), and `os.Lstat(filepath.Join(dir, "link"))` resolves to `/tmp/outside-<random>`.
2. Extraction B (simulating cache reuse into same `dir`): build a second tar+zstd archive containing a regular file entry named `link/payload` with attacker content. Call `extractor.Extract` on the same `dir` — assert that today the file `/tmp/outside-<random>/payload` is created (escape), while the fix should cause `Extract` to return an error such as "cannot be extracted outside of chroot" and no file should exist outside `dir`.

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
