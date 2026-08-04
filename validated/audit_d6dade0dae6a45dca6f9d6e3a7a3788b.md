### Title
Legacy zip extractor (`helpers/archives/zip_extract.go`) performs no path-containment check, allowing zip-slip write outside `BuildDir` - (File: helpers/archives/zip_extract.go)

### Summary
Artifacts downloaded via `dependencies:`/artifact references are extracted by one of several pluggable extractors. Unlike the tar/tarzstd extractor, which explicitly validates each entry resolves inside the target `dir` (`chroot`), the legacy zip extractor (`archive/zip` based) extracts every `zip.File.Name` verbatim with no `dir` join and no traversal/absolute-path check, so a crafted artifact zip with `../` or absolute-path entries can write files outside the intended `BuildDir`.

### Finding Description
`commands/helpers/artifacts_downloader.go`'s `Execute` downloads the artifact archive (retrieved from the GitLab API based on the job's `dependencies:`/`needs:` artifact reference — a target the runner does not itself validate for content) and calls `archive.NewExtractor(format, f, size, wd)`, then `extractor.Extract(ctx)` [1](#0-0) .

For the zip format, `commands/helpers/archive/ziplegacy/zip_legacy_extractor.go`'s `Extract` opens a `zip.Reader` and calls `archives.ExtractZipArchive(zr)` — notably **it never uses `e.dir` at all** when performing extraction [2](#0-1) .

`helpers/archives/zip_extract.go`'s `extractZipFile`/`extractZipFileEntry`/`extractZipSymlinkEntry`/`extractZipDirectoryEntry` all operate directly on `file.Name` (the raw zip entry name) — calling `os.MkdirAll(filepath.Dir(file.Name), ...)`, `os.OpenFile(file.Name, ...)`, and `os.Symlink(string(data), file.Name)` with **no `filepath.Clean`/prefix check against any root directory** [3](#0-2) . The only sanity check applied is `errorIfGitDirectory`, which only flags `.git` paths and does not block traversal [4](#0-3) .

This is in stark contrast to the tar/zstd extractor, which explicitly computes `filepath.Abs(filepath.Join(e.dir, hdr.Name))` and rejects any entry that doesn't stay under `e.dir` with `"%s cannot be extracted outside of chroot (%s)"` [5](#0-4) . The fastzip-based extractor (`commands/helpers/archive/fastzip/zip_fastzip_extractor.go`) delegates directly to the `saracen/fastzip` library, which is expected to perform its own path-containment checks, but the in-tree legacy zip path (`ziplegacy`) has none.

Because `ExtractZipArchive` runs relative to the process's current working directory (since `dir` is discarded) rather than being confined to `FullProjectDir()`/`BuildDir`, a zip entry such as `../../other-project/file` or an absolute path (`/home/other/.ssh/authorized_keys` or a Windows UNC path) is written verbatim to that location, escaping the build directory sandbox that the invariant requires artifacts to stay within.

### Impact Explanation
If the legacy zip extractor path is exercised (e.g., as a fallback for environments where `fastzip` isn't available/enabled, or via feature-flag/older extractor selection), an attacker who controls the content of any artifact zip that another job downloads (through `dependencies:`/`needs:` artifacts) can overwrite arbitrary files reachable by the job's OS user outside the job's `BuildDir`, including files belonging to concurrently-checked-out sibling project directories on a shared runner host, or attacker-chosen absolute paths. This directly violates the "file operations must stay within intended build/cache/artifact roots" invariant with a concrete, reproducible artifact-based write-outside-sandbox primitive.

### Likelihood Explanation
Preconditions: the runner must use the ziplegacy extractor (rather than fastzip) to process the artifact zip, and the archive is produced by GitLab and served to `artifacts_downloader`. Any pipeline author who can produce a job artifact (their own job in a project they can push to) fully controls the zip entry names in the resulting artifact, so crafting `../` or absolute-path entries is trivial and requires no special privilege beyond running a normal CI job. The only variable is whether the vulnerable legacy zip code path is reached in a given deployment; where it is (e.g. explicit fallback logic in `archive.go` or a feature flag toggling extractors), the exploit is fully deterministic and repeatable.

### Recommendation
Add the same containment check used in `tarzstd_extractor.go` to `helpers/archives/zip_extract.go`: resolve each `file.Name` against the intended extraction root with `filepath.Abs(filepath.Join(dir, file.Name))`, verify the result has `dir` as prefix (or equals `dir`), and reject/skip entries that don't, for regular files, directories, and symlink targets alike (also validate symlink target destinations, not just the link path). Additionally, plumb the `dir` parameter from `NewExtractor` into `ExtractZipArchive`/`extractZipFile` instead of silently discarding it.

### Proof of Concept
```go
func TestExtractZipArchive_RejectsPathTraversal(t *testing.T) {
    tmpDir := t.TempDir()
    zipPath := filepath.Join(tmpDir, "artifacts.zip")

    // Build malicious zip with a traversal entry
    f, _ := os.Create(zipPath)
    zw := zip.NewWriter(f)
    w, _ := zw.Create("../../evil-outside-buildDir.txt")
    w.Write([]byte("pwned"))
    zw.Close()
    f.Close()

    extractDir := filepath.Join(tmpDir, "build")
    os.MkdirAll(extractDir, 0755)

    os.Chdir(extractDir) // simulate BuildDir as CWD
    r, _ := zip.OpenReader(zipPath)
    err := archives.ExtractZipArchive(&r.Reader)

    // Expected (currently failing): traversal entries must be rejected,
    // and the file must not exist outside extractDir.
    _, statErr := os.Stat(filepath.Join(tmpDir, "evil-outside-buildDir.txt"))
    assert.True(t, os.IsNotExist(statErr), "traversal entry escaped BuildDir")
    assert.Error(t, err, "extraction should fail on path traversal entries")
}
```
This test currently would fail the assertions (the file gets created outside `extractDir` and `err` is `nil`), confirming the missing path-containment check in `helpers/archives/zip_extract.go`.

### Citations

**File:** commands/helpers/artifacts_downloader.go (L125-140)
```go
	f, size, format, err := openArchive(file.Name())
	if err != nil {
		logrus.Fatalln(err)
	}
	defer f.Close()

	extractor, err := archive.NewExtractor(format, f, size, wd)
	if err != nil {
		logrus.Fatalln(err)
	}

	// Extract artifacts file
	err = extractor.Extract(context.Background())
	if err != nil {
		logrus.Fatalln(err)
	}
```

**File:** commands/helpers/archive/ziplegacy/zip_legacy_extractor.go (L26-33)
```go
func (e *extractor) Extract(ctx context.Context) error {
	zr, err := zip.NewReader(e.r, e.size)
	if err != nil {
		return err
	}

	return archives.ExtractZipArchive(zr)
}
```

**File:** helpers/archives/zip_extract.go (L12-66)
```go
func extractZipDirectoryEntry(file *zip.File) (err error) {
	err = os.Mkdir(file.Name, file.Mode().Perm())

	// The "directory does exist" error is not an error for us
	if os.IsExist(err) {
		err = nil
	}
	return
}

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

func extractZipFileEntry(file *zip.File) (err error) {
	var out *os.File
	in, err := file.Open()
	if err != nil {
		return err
	}
	defer func() { _ = in.Close() }()

	// Remove file before creating a new one, otherwise we can error that file does exist
	_ = os.Remove(file.Name)
	out, err = os.OpenFile(file.Name, os.O_WRONLY|os.O_CREATE|os.O_TRUNC, file.Mode().Perm())
	if err != nil {
		return err
	}
	defer func() { _ = out.Close() }()
	_, err = io.Copy(out, in)

	return
}

func extractZipFile(file *zip.File) (err error) {
	// Create all parents to extract the file
	err = os.MkdirAll(filepath.Dir(file.Name), 0o777)
	if err != nil {
		return err
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
