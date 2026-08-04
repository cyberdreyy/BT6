### Title
Unvalidated symlink target in zip cache extraction allows write-path escape outside the job's cache directory - ([File: helpers/archives/zip_extract.go])

### Summary
`extractZipSymlinkEntry` in `helpers/archives/zip_extract.go` creates a filesystem symlink using the raw byte content of the zip entry as the link target, with no validation that the target stays inside the extraction root. `ExtractZipArchive`, which calls this function for every `os.ModeSymlink` entry, also performs no root-containment check on `file.Name` or the symlink target, unlike the `tarzstd` extractor which at least validates the entry name (though not the link target) against its `e.dir` chroot.

### Finding Description
`extractZipSymlinkEntry` reads the entry content via `file.Open()`/`io.ReadAll` and calls `os.Symlink(string(data), file.Name)` unconditionally: [1](#0-0) 

`ExtractZipArchive` iterates the zip's files and dispatches symlink entries straight to this function, with no path-containment check on either the entry name or the eventual link target — only a `.git` directory warning check (`errorIfGitDirectory`) exists, which is unrelated to path escape: [2](#0-1) 

This is invoked by `commands/helpers/cache_extractor.go`'s `Execute`, which downloads the cache zip and extracts it directly into the process's current working directory (the job's cache directory) via `archive.NewExtractor(...).Extract(...)`: [3](#0-2) 

and `ziplegacy.extractor.Extract` simply calls `archives.ExtractZipArchive(zr)` with no directory-scoping wrapper at all: [4](#0-3) 

By contrast, the `tarzstd` extractor at least resolves and validates the *entry path* against its `e.dir` chroot before creating anything: [5](#0-4) 
but notably even that extractor does **not** validate the symlink *target* (`hdr.Linkname`) before calling `os.Symlink`: [6](#0-5) 

So for zip cache archives there is no containment check whatsoever (neither on entry name nor target), and even the more careful tar path only restricts where the symlink node is *created*, not what it points to. An attacker who controls a job that populates a cache archive (i.e., any pipeline author for that project, or a job that later restores a cache in a shared-cache namespace) can include a symlink entry whose name is a normal relative filename (so it lands inside the current project's cache directory) but whose target content is a `../`-relative or absolute path pointing outside that directory — e.g., into a sibling directory on a shared runner filesystem (another project's cache dir under a predictable layout such as `<cache-root>/<runner-id>/<project-id>/<cache-key>`, or the job's own `BuildDir` tree). Once that symlink exists on disk, any subsequent file write that happens to touch the same relative filename (e.g., a build script writing an "output" file, or a later cache/artifact operation reusing the same well-known filename) will transparently follow the symlink and land at the attacker-chosen location instead of inside the sandboxed cache/build root.

### Impact Explanation
On shared runners using executors with a shared/persistent filesystem (e.g., shell executor, or any executor where cache directories share a common root across projects), this allows a job to plant a symlink that redirects subsequent file writes (build output, later cache archive contents, etc.) outside the job's own cache/build root and into another project's directory tree, or vice versa — violating the invariant that "file operations must stay within intended build/cache/artifact roots" and that "a normal job must not... access another project's workload." The severity is bounded by the requirement that the attacker guess or know the target path (project ID/cache key layout), and that a subsequent write actually reuses the planted relative filename.

### Likelihood Explanation
Preconditions: the runner uses an executor/configuration where cache extraction happens on a filesystem shared across projects/jobs (e.g., shell executor on a shared host, or any local/shared cache root), the attacker knows or can predict the target path layout, and a later operation performs a write using the same relative filename as the planted symlink. These preconditions are plausible but not universal (containerized executors with per-job filesystem isolation are not affected). The zip-crafting step itself is trivial and fully attacker-controlled (any job can produce/upload its own cache archive).

### Recommendation
In `extractZipSymlinkEntry` (and the equivalent tar/zstd symlink handling), resolve the intended extraction root (the `wd`/`dir` passed to the extractor) and validate that both the entry name and the resolved symlink target stay within that root before calling `os.Symlink`, rejecting (or rewriting) entries whose target would escape via `../` or absolute paths — mirroring the containment check already used in `tarzstd_extractor.go` for entry names, but applying it to link targets as well as entry paths in all archive extractors (zip and tar).

### Proof of Concept
```go
func TestExtractZipSymlinkEntry_EscapesRoot(t *testing.T) {
    root := t.TempDir()
    prevWd, _ := os.Getwd()
    require.NoError(t, os.Chdir(root))
    defer os.Chdir(prevWd)

    var buf bytes.Buffer
    zw := zip.NewWriter(&buf)
    hdr := &zip.FileHeader{Name: "output"}
    hdr.SetMode(os.ModeSymlink | 0777)
    w, _ := zw.CreateHeader(hdr)
    // symlink target escapes the extraction root
    w.Write([]byte("../victim-project/cache/output"))
    zw.Close()

    zr, err := zip.NewReader(bytes.NewReader(buf.Bytes()), int64(buf.Len()))
    require.NoError(t, err)
    require.NoError(t, ExtractZipArchive(zr))

    target, err := os.Readlink(filepath.Join(root, "output"))
    require.NoError(t, err)
    // Assert failure of containment: target resolves outside root
    resolved := filepath.Clean(filepath.Join(root, target))
    assert.False(t, strings.HasPrefix(resolved, root+string(filepath.Separator)),
        "symlink target escaped extraction root: %s", resolved)
}
```
Expected (current, buggy) result: the assertion fails to hold — i.e., `resolved` is outside `root`, proving the symlink target is unconstrained. After a fix that validates targets against the extraction root, the test should be updated to assert `ExtractZipArchive` returns an error or skips the entry instead of creating the escaping symlink.

### Citations

**File:** helpers/archives/zip_extract.go (L22-39)
```go
func extractZipSymlinkEntry(file *zip.File) (err error) {
	var data []byte
	in, err := file.Open()
	if err != nil {
		return err
	}
	defer func() { _ = in.Close() }()

	data, err = io.ReadAll(in)
	if err != nil {
		return err
	}

	// Remove symlink before creating a new one, otherwise we can error that file does exist
	_ = os.Remove(file.Name)
	err = os.Symlink(string(data), file.Name)
	return
}
```

**File:** helpers/archives/zip_extract.go (L85-96)
```go
func ExtractZipArchive(archive *zip.Reader) error {
	tracker := newPathErrorTracker()

	for _, file := range archive.File {
		if err := errorIfGitDirectory(file.Name); tracker.actionable(err) {
			printGitArchiveWarning("extract")
		}

		if err := extractZipFile(file); tracker.actionable(err) {
			logrus.Warningf("%s: %s (suppressing repeats)", file.Name, err)
		}
	}
```

**File:** commands/helpers/cache_extractor.go (L646-663)
```go
	f, size, format, err := openArchive(c.File)
	if os.IsNotExist(err) {
		warningln("Cache file does not exist")
	}
	if err != nil {
		logrus.Fatalln(err)
	}
	defer f.Close()

	extractor, err := archive.NewExtractor(format, f, size, wd)
	if err != nil {
		logrus.Fatalln(err)
	}

	err = extractor.Extract(context.Background())
	if err != nil {
		logrus.Fatalln(err)
	}
```

**File:** commands/helpers/archive/ziplegacy/zip_legacy_extractor.go (L24-33)
```go
// Extract extracts files from the reader to the directory passed to
// NewZipExtractor.
func (e *extractor) Extract(ctx context.Context) error {
	zr, err := zip.NewReader(e.r, e.size)
	if err != nil {
		return err
	}

	return archives.ExtractZipArchive(zr)
}
```

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

**File:** commands/helpers/archive/tarzstd/tarzstd_extractor.go (L113-117)
```go
		if fi.Mode()&os.ModeSymlink != 0 {
			if err := os.Symlink(hdr.Linkname, path); err != nil {
				return err
			}
		}
```
