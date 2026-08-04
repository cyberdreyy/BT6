### Title
Tar symlink extraction only validates entry path, not symlink target, allowing an out-of-tree symlink to be planted in the job workspace - ([File: commands/helpers/archive/tarzstd/tarzstd_extractor.go])

### Summary
`extractor.Extract` validates that a tar entry's *name* (`hdr.Name`) resolves inside `e.dir` before acting on it, but for symlink entries it never validates `hdr.Linkname` (the symlink target) before calling `os.Symlink(hdr.Linkname, path)`. This lets a crafted artifact/cache archive plant a symlink whose location is inside the job workspace but whose target is an arbitrary absolute path or a `../`-escaping relative path.

### Finding Description
In `Extract`, the chroot check only covers the destination path derived from `hdr.Name`: [1](#0-0) 

For symlink entries, the header is simply deferred and later materialized with the raw, unvalidated `hdr.Linkname`: [2](#0-1) 

There is no check anywhere in this function (nor in `updateFileMetadata`) that constrains `hdr.Linkname` to stay within `e.dir`. An attacker who can control an artifact or cache archive extracted by Runner (e.g., a `.gitlab-ci.yml` author defining `artifacts`/`cache` that get downloaded and re-extracted, or a cache archive shared across pipelines/projects) can include a tar entry such as:
- `Name: "link"`, `Typeflag: TypeSymlink`, `Linkname: "/etc/passwd"`, or
- `Name: "link"`, `Typeflag: TypeSymlink`, `Linkname: "../../../../etc/passwd"`

`path` for `"link"` passes the chroot check (it resolves under `e.dir`), so the entry proceeds to the deferred map and `os.Symlink("/etc/passwd", path)` succeeds, creating `e.dir/link -> /etc/passwd`. The comparison against `zip_extract.go`'s `extractZipSymlinkEntry` shows the same unrestricted pattern (`os.Symlink(string(data), file.Name)`), confirming this is a design gap in symlink handling rather than an isolated typo.

### Impact Explanation
Creating the symlink itself does not read or write the target — it is a dangling link creation, so extraction alone causes no direct read/write outside `e.dir`. The concrete impact only materializes if a subsequent, Runner-driven step *dereferences* that symlink while operating on paths under the job workspace (e.g., a later helper that walks/reads `e.dir` following symlinks, such as artifact packaging, cache archiving, or file-copy helpers that don't use `lstat`/`O_NOFOLLOW`). Whether such a follow-on dereference actually occurs depends on code outside this function; this function by itself only plants the link.

### Likelihood Explanation
Preconditions are low-friction for an unprivileged pipeline author: any job that defines `artifacts`/`cache` content is fully attacker-controlled and passes through this extractor when restored. Building the crafted tar+zstd stream with a symlink header and arbitrary `Linkname` is straightforward and repeatable.

### Recommendation
Validate `hdr.Linkname` the same way `hdr.Name` is validated: for relative link targets, resolve `filepath.Join(filepath.Dir(path), hdr.Linkname)` and reject/reroute if it escapes `e.dir`; for absolute targets, reject outright unless explicitly allowed. Apply the same fix to `extractZipSymlinkEntry` in `helpers/archives/zip_extract.go` for consistency.

### Proof of Concept
Go unit test in `tarzstd` package:
1. Build an in-memory tar+zstd stream containing one symlink entry with `Name: "evil"` and `Linkname: "/etc/passwd"`.
2. Call `NewExtractor(...).Extract(ctx)` against a temp `dir`.
3. Assert either `Extract` returns an error (rejecting the entry), or, if it succeeds, assert `os.Readlink(filepath.Join(dir, "evil"))` resolves to a path under `dir` (fails today since it will be `/etc/passwd`).
4. Repeat with `Linkname: "../../outside"` and assert the resolved absolute target stays under `dir`.

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
