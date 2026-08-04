Confirmed finding: the `archive.NewArchiver` doc comment claims "The archiver will ensure that files to be archived are children of the directory provided" [1](#0-0) , but the `ziplegacy` implementation does not enforce this invariant.

### Title
Missing root-containment check in ziplegacy `Archive` allows base-path confusion (defense-in-depth gap) - (File: commands/helpers/archive/ziplegacy/zip_legacy_archiver.go)

### Summary
`ziplegacy.archiver.Archive` receives a `dir` field (the workspace root) but never uses it to validate that entries in the `files` map are actually located under that root, unlike the `tarzstd` archiver which explicitly enforces `strings.HasPrefix(path, a.dir+string(filepath.Separator))` before archiving each file [2](#0-1) . The `ziplegacy.Archive` function simply sorts map keys and forwards them straight to `archives.CreateZipArchive` with no root check [3](#0-2) .

### Finding Description
The `Archiver` interface contract documented in `archive.go` promises callers that "the archiver will ensure that files to be archived are children of the directory provided" [1](#0-0) . The `tarzstd` implementation honors this by resolving each file to an absolute path and rejecting any path outside `a.dir` [2](#0-1) . The `ziplegacy`, `gziplegacy`, and `raw` archivers do not perform this check at all — they store `dir` in the struct but it is dead/unused for validation purposes [4](#0-3) [5](#0-4) [6](#0-5) .

However, tracing the actual callers (`cache_archiver.go` and `artifacts_uploader.go`), the `files` map passed into `Archive` is always built by `fileArchiver.enumerate()` → `processPath` → `findRelativePathInProject`, which resolves each candidate to an absolute path and explicitly rejects any path whose `filepath.Rel(c.wd, abs)` result begins with `".."` [7](#0-6) , and `process()` performs an equivalent check again before calling `c.add()` [8](#0-7) . Both `cache_archiver.go`'s `createZipFile` and `artifacts_uploader.go`'s `createBodyProvider` pass `c.files` (built exclusively through this validated path) to `archiver.Archive` [9](#0-8) [10](#0-9) . There is no code path found where an archiver is invoked with attacker-supplied absolute or `..`-relative filenames bypassing `fileArchiver`'s validation. Symlinks whose *own path* resides inside the project directory pass this check (since `filepath.Abs`/`filepath.Rel` operate on the literal path, not the resolved target), but `createZipEntry`/`createZipSymlinkEntry` store such entries as symlink records (writing the raw `os.Readlink` target string as the entry content) rather than dereferencing and packaging the external target's file contents [11](#0-10) [12](#0-11) , so no external file *content* is exfiltrated through this specific archiving path — that is a symlink-on-extraction concern, not an inclusion-during-archiving one.

So while the missing containment check in `ziplegacy.Archive` is a genuine deviation from the documented interface contract and a latent defense-in-depth gap (any future caller that populates `files` from a less-strict source, or a bug introduced elsewhere in `fileArchiver`, would have no second line of defense), it does not currently constitute an independently reachable, attacker-triggerable path-traversal in the audited code, because the only production callers pre-validate all paths against the workspace root before the map ever reaches `Archive`.

### Impact Explanation
No concrete secret exposure or archive poisoning is currently reachable through `ziplegacy.Archive` alone, since the upstream `fileArchiver` enforces root containment before this function is ever called. The residual risk is architectural: `ziplegacy`, `gziplegacy`, and `raw` archivers silently rely on caller discipline instead of self-enforcing the invariant documented in `archive.go`, unlike `tarzstd`.

### Likelihood Explanation
Not currently exploitable via the audited entrypoints (`cache-archiver`, `artifacts-uploader`) because `findRelativePathInProject`/`process` in `file_archiver.go` reject any `..`-escaping or absolute-outside-root path before it reaches the `files` map [13](#0-12) . Exploitability would require a different, currently-nonexistent code path that constructs the `files` map without going through `fileArchiver`.

### Recommendation
Add the same root-containment check used in `tarzstd_archiver.go` to `ziplegacy`, `gziplegacy`, and `raw` archivers so the invariant documented in `archive.go`'s `NewArchiver` comment is actually enforced in every implementation, rather than relying solely on `fileArchiver` upstream validation.

### Proof of Concept
Go unit test (`ziplegacy` package): construct `files := map[string]os.FileInfo{"/etc/passwd": fi}` with `a.dir` set to a temp project directory not containing `/etc/passwd`, call `a.Archive(ctx, files)`, and assert it returns an error (currently it will succeed and include `/etc/passwd` in the zip — proving the missing check exists at the unit level, even though it is not reachable end-to-end today). Contrast with an equivalent test against `tarzstd.archiver.Archive`, which correctly returns `"... cannot be archived from outside of chroot ..."` [14](#0-13) .

### Citations

**File:** commands/helpers/archive/archive.go (L86-97)
```go
// NewArchiver returns a new Archiver of the specified format.
//
// The archiver will ensure that files to be archived are children of the
// directory provided.
func NewArchiver(format Format, w io.Writer, dir string, level CompressionLevel) (Archiver, error) {
	fn := archivers[format]
	if fn == nil {
		return nil, fmt.Errorf("%q format: %w", format, ErrUnsupportedArchiveFormat)
	}

	return fn(w, dir, level)
}
```

**File:** commands/helpers/archive/tarzstd/tarzstd_archiver.go (L68-74)
```go
		path, err := filepath.Abs(name)
		if err != nil {
			return err
		}
		if !strings.HasPrefix(path, a.dir+string(filepath.Separator)) && path != a.dir {
			return fmt.Errorf("%s cannot be archived from outside of chroot (%s)", name, a.dir)
		}
```

**File:** commands/helpers/archive/ziplegacy/zip_legacy_archiver.go (L24-43)
```go
type archiver struct {
	w   io.Writer
	dir string
}

// NewArchiver returns a new Zip Archiver.
func NewArchiver(w io.Writer, dir string, level archive.CompressionLevel) (archive.Archiver, error) {
	return &archiver{w: w, dir: dir}, nil
}

// Archive archives all files as new gzip streams.
func (a *archiver) Archive(ctx context.Context, files map[string]os.FileInfo) error {
	sorted := make([]string, 0, len(files))
	for filename := range files {
		sorted = append(sorted, filename)
	}
	sort.Strings(sorted)

	return archives.CreateZipArchive(a.w, sorted)
}
```

**File:** commands/helpers/archive/gziplegacy/gzip_legacy_archiver.go (L17-37)
```go
// archiver is a gzip stream archiver.
type archiver struct {
	w   io.Writer
	dir string
}

// NewArchiver returns a new Gzip Archiver.
func NewArchiver(w io.Writer, dir string, level archive.CompressionLevel) (archive.Archiver, error) {
	return &archiver{w: w, dir: dir}, nil
}

// Archive archives all files as new gzip streams.
func (a *archiver) Archive(ctx context.Context, files map[string]os.FileInfo) error {
	sorted := make([]string, 0, len(files))
	for filename := range files {
		sorted = append(sorted, filename)
	}
	sort.Strings(sorted)

	return archives.CreateGzipArchive(a.w, sorted)
}
```

**File:** commands/helpers/archive/raw/raw_archiver.go (L22-52)
```go
type archiver struct {
	w   io.Writer
	dir string
}

// NewArchiver returns a new Raw Archiver.
func NewArchiver(w io.Writer, dir string, level archive.CompressionLevel) (archive.Archiver, error) {
	return &archiver{w: w, dir: dir}, nil
}

// Archive opens and copies a single file to the writer passed to
// NewRawArchiver. If more than one file is passed, ErrTooManyRawFiles is
// returned.
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
}
```

**File:** commands/helpers/file_archiver.go (L191-221)
```go
func (c *fileArchiver) findRelativePathInProject(path string) (string, error) {
	slashPath := filepath.ToSlash(path)
	if filepath.Clean(slashPath) == filepath.Clean(c.wd) {
		return ".", nil
	}

	base, patt := slashPath, ""
	// check if path contains a glob pattern
	if strings.ContainsAny(slashPath, "*?[{") {
		base, patt = doublestar.SplitPattern(slashPath)
	}

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

	// Relative path is needed now that our fsys "root" is at the working directory
	rel = filepath.Join(rel, patt)
	rel = filepath.FromSlash(rel)
	return rel, nil
```

**File:** commands/helpers/cache_archiver.go (L229-238)
```go
	archiver, err := archive.NewArchiver(archive.Format(c.CompressionFormat), f, c.wd, GetCompressionLevel(c.CompressionLevel))
	if err != nil {
		return 0, err
	}

	// Create archive
	err = archiver.Archive(context.Background(), c.files)
	if err != nil {
		return 0, err
	}
```

**File:** commands/helpers/artifacts_uploader.go (L116-126)
```go
			archiver, archiveErr := archive.NewArchiver(archive.Format(format), pw, c.wd, GetCompressionLevel(c.CompressionLevel))
			if archiveErr != nil {
				pr.CloseWithError(archiveErr)
				return nil, archiveErr
			}

			// Start a new Goroutine to create the archive for this attempt
			go func() {
				archiveErr := archiver.Archive(context.Background(), c.files)
				pw.CloseWithError(archiveErr)
			}()
```

**File:** helpers/archives/zip_create.go (L68-83)
```go
	switch fi.Mode() & os.ModeType {
	case os.ModeDir:
		return createZipDirectoryEntry(archive, fh)

	case os.ModeSymlink:
		return createZipSymlinkEntry(archive, fh)

	case os.ModeNamedPipe, os.ModeSocket, os.ModeDevice:
		// Ignore files of these types
		logrus.Warningln("File ignored:", fileName)
		return nil

	default:
		return createZipFileEntry(archive, fh)
	}
}
```
