### Title
Raw archiver blindly follows symlinks and performs no base-directory containment check, unlike other archivers - (File: commands/helpers/archive/raw/raw_archiver.go)

### Summary
`raw.archiver.Archive` opens the given pathname directly with `os.Open` and copies its content, without ever checking the file's `os.FileInfo` mode for a symlink or validating that the resolved path is still inside `a.dir`. Every other archiver in the same package (e.g. `tarzstd`) both enforces a base-directory containment check and refuses to dereference symlink content, so `raw_archiver.go` is inconsistent with the rest of the archive package's security model.

### Finding Description
The file selection/filtering stage (`commands/helpers/file_archiver.go`) builds the `files` map that is handed to an `Archiver.Archive()` call. Its containment check, `findRelativePathInProject`, is purely lexical: it computes `filepath.Abs`/`filepath.Rel` against `c.wd` and rejects paths whose *relative name* starts with `..` [1](#0-0) . Critically, the file entry itself is recorded using `os.Lstat`, not `os.Stat`, so a symlink whose *name* lives inside the workspace passes this check regardless of where the symlink target points [2](#0-1) .

Downstream, the `tarzstd` archiver re-validates the resolved absolute path against `a.dir` with an explicit prefix check, and — for symlink-mode entries — only records the link target in the tar header without ever opening/copying the target's content (`fi.Mode().IsRegular()` guard skips `io.Copy` for symlinks) [3](#0-2) .

`raw.archiver.Archive`, however, does neither. It accepts a `dir` field (`a.dir`) at construction time [4](#0-3) , but the field is never referenced inside `Archive`. The function simply does `os.Open(pathname)` and `io.Copy` for whatever pathname is present in the `files` map, with no mode check and no chroot-style validation at all [5](#0-4) . Because `os.Open` follows symlinks by default, a symlink entry that passed the lexical filtering stage (its own name is inside the project workspace) will have its **target** content read and packaged — even if that target lives in a sibling worktree, a sibling project checkout, or any other path on the filesystem the job process can read.

The archiving entrypoints (`CacheArchiverCommand.createZipFile` and `ArtifactsUploaderCommand.createBodyProvider`) both pass `c.wd` as the `dir` argument to `archive.NewArchiver`, which is the same base used for filtering [6](#0-5) [7](#0-6) , so nominally the "filtering base" and "packaging base" are the same value — but the raw archiver never uses that value to re-validate, so the invariant is enforced only by the earlier, symlink-unaware lexical filter. Any pipeline step that can place a symlink inside the workspace pointing at a sibling worktree/project path (fully within an unprivileged job author's control) defeats that filter.

### Impact Explanation
An unprivileged pipeline author can craft a job that creates a symlink inside the checkout pointing to files outside the project workspace (e.g., a sibling git worktree or another project's checkout co-located on the same runner host/build directory), then references that symlink via `artifacts:paths`/cache paths. When the raw archiver format is used to package that entry, the target file's content is read across the intended workspace boundary and shipped into the artifact/cache archive, resulting in cross-project data disclosure equivalent to artifact/cache poisoning or exfiltration of another project's build data.

### Likelihood Explanation
The precondition is a shared build-directory host and sibling checkouts/worktrees existing on disk relative to the job's workspace, which is a normal Runner deployment layout (multiple projects/branches building on the same host/executor). Creating a symlink in a job's own script is trivial for any pipeline author. The bug is deterministic and repeatable given those preconditions.

### Recommendation
In `raw.archiver.Archive`, before opening `pathname`: (1) validate the resolved absolute path against `a.dir` the same way `tarzstd` does (`filepath.Abs` + prefix check), and (2) check `fi.Mode()&os.ModeSymlink` and refuse (or explicitly re-validate the symlink target against the base directory) rather than silently following it via `os.Open`.

### Proof of Concept
Go unit test to add near `commands/helpers/archiver_test.go`:
1. Create `dirA := t.TempDir()` (the job workspace) and `dirB := t.TempDir()` (a sibling worktree) with a secret file `dirB/secret.txt`.
2. Inside `dirA`, create a symlink `dirA/link` → `dirB/secret.txt`.
3. Build `files := map[string]os.FileInfo{"link": lstatInfo}` using `os.Lstat("link")` (mirroring `fileArchiver.add`).
4. Call `raw.NewArchiver(buf, dirA, ...)` then `archiver.Archive(ctx, files)`.
5. Assert: the resulting `buf` contains the bytes of `dirB/secret.txt` — demonstrating that content from outside `dirA` was packaged, which should instead fail/be rejected.

### Citations

**File:** commands/helpers/file_archiver.go (L127-138)
```go
func (c *fileArchiver) add(path string) error {
	// Always use slashes
	path = filepath.ToSlash(path)

	// Check if file exist
	info, err := os.Lstat(path)
	if err == nil {
		c.files[path] = info
	}

	return err
}
```

**File:** commands/helpers/file_archiver.go (L203-216)
```go
	abs, err := filepath.Abs(base)
	if err != nil {
		return "", fmt.Errorf("could not resolve artifact absolute path %s: %w", path, err)
	}

	rel, err := filepath.Rel(c.wd, abs)
	if err != nil {
		return "", fmt.Errorf("could not resolve artifact relative path %s: %w", path, err)
	}

	// If fully resolved relative path begins with ".." it is not a subpath of our working directory
	if strings.HasPrefix(rel, ".."+string(filepath.Separator)) || rel == ".." {
		return "", fmt.Errorf("artifact path is not a subpath of project directory: %s", path)
	}
```

**File:** commands/helpers/archive/tarzstd/tarzstd_archiver.go (L62-106)
```go
	for _, name := range sorted {
		fi := files[name]
		if fi.Mode()&irregularModes != 0 {
			continue
		}

		path, err := filepath.Abs(name)
		if err != nil {
			return err
		}
		if !strings.HasPrefix(path, a.dir+string(filepath.Separator)) && path != a.dir {
			return fmt.Errorf("%s cannot be archived from outside of chroot (%s)", name, a.dir)
		}

		rel, err := filepath.Rel(a.dir, path)
		if err != nil {
			return err
		}

		if ctx.Err() != nil {
			return ctx.Err()
		}

		var link string
		if fi.Mode()&os.ModeSymlink != 0 {
			link, err = os.Readlink(path)
			if err != nil {
				return err
			}
		}

		hdr, err := tar.FileInfoHeader(fi, link)
		if err != nil {
			return err
		}
		hdr.Name = rel
		if fi.IsDir() {
			hdr.Name += "/"
		}

		if err := tw.WriteHeader(hdr); err != nil {
			return err
		}

		if !fi.Mode().IsRegular() {
```

**File:** commands/helpers/archive/raw/raw_archiver.go (L27-30)
```go
// NewArchiver returns a new Raw Archiver.
func NewArchiver(w io.Writer, dir string, level archive.CompressionLevel) (archive.Archiver, error) {
	return &archiver{w: w, dir: dir}, nil
}
```

**File:** commands/helpers/archive/raw/raw_archiver.go (L35-51)
```go
func (a *archiver) Archive(ctx context.Context, files map[string]os.FileInfo) error {
	if len(files) > 1 {
		return ErrTooManyRawFiles
	}

	for pathname := range files {
		f, err := os.Open(pathname)
		if err != nil {
			return err
		}
		defer f.Close()

		_, err = io.Copy(a.w, f)
		return err
	}

	return nil
```

**File:** commands/helpers/cache_archiver.go (L229-229)
```go
	archiver, err := archive.NewArchiver(archive.Format(c.CompressionFormat), f, c.wd, GetCompressionLevel(c.CompressionLevel))
```

**File:** commands/helpers/artifacts_uploader.go (L116-116)
```go
			archiver, archiveErr := archive.NewArchiver(archive.Format(format), pw, c.wd, GetCompressionLevel(c.CompressionLevel))
```
