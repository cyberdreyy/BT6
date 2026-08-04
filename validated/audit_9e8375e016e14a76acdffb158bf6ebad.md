### Title
Symlink component in tar path bypasses lexical chroot check, allowing `updateFileMetadata` (and prior write) to escape into predictable sibling build/cache directory - (File: commands/helpers/archive/tarzstd/tarzstd_extractor.go)

### Summary
`extractor.Extract` validates each tar entry's destination path only lexically (`filepath.Join(e.dir, hdr.Name)` + prefix check against `e.dir`) before creating files, directories, symlinks and calling `updateFileMetadata`. This check never resolves symlinks, so if a directory component of the destination path is a pre-existing symlink pointing outside `e.dir`, subsequent filesystem calls (`os.MkdirAll`, `os.Create`, and the metadata calls in `updateFileMetadata`) transparently follow that symlink and operate on the resolved location outside the job's directory.

### Finding Description
The path-confinement check is purely string-based: [1](#0-0) 
It never calls anything like `filepath.EvalSymlinks` and never checks whether a parent path segment is a symlink. Symlink entries are collected into a `deferred` map and only materialized with `os.Symlink(hdr.Linkname, path)` after the whole archive has been scanned once, with `hdr.Linkname` never validated to stay inside `e.dir`: [2](#0-1) 
Because symlink creation is deferred to the end of a single `Extract()` call, a single archive cannot plant-then-use a symlink in one pass. However, `e.dir` (the job's build/cache directory) is reused across multiple, separate extraction invocations within the same job's lifetime (e.g. multiple `Cache` entries, artifacts of dependent jobs, or repeated cache-extractor runs into the same `CacheDir`/`BuildDir`). If a first archive plants a symlink such as `foo -> ../../other-project-dir` inside `e.dir`, and a second archive (processed by a later `cache-extractor`/`artifact-extractor` invocation in the same job) contains an entry named `foo/evil`, the lexical check on `filepath.Join(e.dir, "foo/evil")` still starts with `e.dir` and passes. But `os.MkdirAll(filepath.Dir(path))` and `os.Create(path)` follow the real, pre-existing `foo` symlink on disk, so the actual write lands at the resolved target outside `e.dir`. `updateFileMetadata` is then called with that same `path` string and performs `lchtimes`, `lchmod`, and `lchown`: [3](#0-2) 
The "l"-prefixed syscalls (`Fchmodat` with `AT_SYMLINK_NOFOLLOW`, `os.Lchown`, `unix.Lutimes` — see `commands/helpers/archive/tarzstd/ops_unix.go`) only avoid following the *final* path component if it is itself a symlink; they still resolve intermediate directory symlinks per standard POSIX path-resolution semantics. So the chown/chmod/utimes calls act on the file at the resolved (outside-`e.dir`) location, exactly matching the described escape.

### Impact Explanation
If exploitable end-to-end, this allows write, chmod, and chown/chtimes tampering of files inside another job's build or cache directory on a shared runner (shell/SSH/custom executors, or any setup where `builds_dir`/`cache_dir` is shared across concurrent jobs), matching the "cross-project or cross-job state tampering" impact class.

### Likelihood Explanation
Exploitability is limited by several hard preconditions that were not fully verifiable from the indexed code:
1. It requires two *separate* archive-extraction invocations into the *same* `e.dir` within one job's lifetime — a single archive cannot exploit this because symlink materialization is strictly deferred past all other entries in `Extract()`.
2. `os.MkdirAll` must actually traverse through the planted symlink without erroring — this needs the symlink target to already exist as a directory, and no intervening cleanup removes the planted symlink between the two extraction calls.
3. Real per-job directories under `builds_dir`/`cache_dir` are namespaced by runner token, concurrency ID, namespace, and project name (e.g. `{builds_dir}/$RUNNER_TOKEN_KEY/$CONCURRENT_PROJECT_ID/$NAMESPACE/$PROJECT_NAME`), so hitting a *specific* sibling job's directory requires the attacker to know or predict that path, which is not generally attacker-controlled information.
4. Whether GitLab Runner's cache/artifact pipeline ever re-extracts a second archive into the *same* `e.dir` that already contains an attacker-planted symlink from a prior extraction (as opposed to a freshly created/cleaned directory per extraction call) was not confirmed from the available code context.

Given these unresolved preconditions, I cannot confirm this is practically reachable as described without further investigation of the caller code paths (`cache-extractor`/`artifact-extractor` commands and whether `BuildsDir`/`CacheDir` cleanup happens between multiple extractions in the same job). The root-cause bug (lexical-only path check, unvalidated symlink targets) is real, but the specific "sibling job directory" impact requires directory-name predictability that is not established here.

### Recommendation
- After computing `path`, resolve it with `filepath.EvalSymlinks` (or check each path component) and re-verify containment inside `e.dir`, rejecting entries where any component is a symlink pointing outside the extraction root.
- Validate `hdr.Linkname` for symlink entries to ensure the resolved target stays within `e.dir` before calling `os.Symlink`.
- Consider using `O_NOFOLLOW`/`openat`-style path resolution (already partially present for metadata ops) consistently for directory creation and file creation as well, not just metadata updates.

### Proof of Concept
Go unit test sketch for `commands/helpers/archive/tarzstd`:
1. Create `jobDir` and a `sibling` directory outside it.
2. First `extractor.Extract` call with a tar stream containing a single symlink entry `link -> ../sibling`.
3. Second `extractor.Extract` call (same `dir: jobDir`) with a tar stream containing a regular file entry `link/pwned` with attacker-controlled content and metadata (uid/gid/mtime).
4. Assert: `filepath.Join(sibling, "pwned")` exists, contains the attacker's content, and has the attacker-specified uid/gid/mtime — proving the write and `updateFileMetadata` call landed outside `jobDir`.
5. Assert failure/absence of escape once the recommended `EvalSymlinks`-based containment check is added.

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

**File:** commands/helpers/archive/tarzstd/tarzstd_extractor.go (L74-122)
```go
		switch {
		case fi.Mode()&os.ModeSymlink != 0:
			deferred[path] = hdr
			continue

		case fi.Mode().IsDir():
			deferred[path] = hdr

			err := os.Mkdir(path, 0777)
			if err != nil && !os.IsExist(err) {
				return err
			}

		case fi.Mode().IsRegular():
			f, err := os.Create(path)
			if err != nil {
				return err
			}

			if _, err := io.Copy(f, tr); err != nil {
				f.Close()
				return err
			}
			if err := f.Close(); err != nil {
				return err
			}

			if err := e.updateFileMetadata(path, hdr); err != nil {
				return err
			}
		}
	}

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

		if err := e.updateFileMetadata(path, hdr); err != nil {
			return err
		}
	}
```

**File:** commands/helpers/archive/tarzstd/tarzstd_extractor.go (L127-140)
```go
func (e *extractor) updateFileMetadata(path string, hdr *tar.Header) error {
	fi := hdr.FileInfo()

	if err := lchtimes(path, fi.Mode(), time.Now(), fi.ModTime()); err != nil {
		return err
	}

	if err := lchmod(path, fi.Mode()); err != nil {
		return err
	}

	_ = lchown(path, hdr.Uid, hdr.Gid)
	return nil
}
```
