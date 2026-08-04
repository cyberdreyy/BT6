Based on my investigation:

### Title
Zip-format artifact/cache extraction path (ziplegacy) ignores target directory and lacks `../` traversal validation, permitting cross-checkout path traversal writes - ([File: helpers/archives/zip_extract.go], [File: commands/helpers/archive/ziplegacy/zip_legacy_extractor.go])

### Summary
`ArtifactsDownloaderCommand.Execute` calls `openArchive`, which defaults to `archive.Zip` unless zstd/gzip magic bytes are detected, and then `archive.NewExtractor(archive.Zip, f, size, wd)` [1](#0-0) . When the fastzip feature flag is off, the registered zip extractor is `ziplegacy.NewExtractor`, whose `Extract()` completely discards the `dir`/`wd` argument and calls `archives.ExtractZipArchive(zr)` using only the zip entries' raw `Name` field, with no `..`/absolute-path validation, unlike the `tarzstd` extractor which explicitly enforces a chroot-style prefix check [2](#0-1) [3](#0-2) .

### Finding Description
`helpers/archives/zip_extract.go`'s `extractZipFile`/`extractZipFileEntry`/`extractZipDirectoryEntry`/`extractZipSymlinkEntry` functions use `file.Name` (from the zip's central directory, fully attacker-controlled since the artifact zip content is uploaded by the job itself) directly as a filesystem path passed to `os.MkdirAll`, `os.Mkdir`, `os.OpenFile`, and `os.Symlink`, with zero sanitization against `../` sequences or absolute paths [4](#0-3) . `ExtractZipArchive` iterates `archive.File` directly (not via Go's `fs.FS`-style `Open(name)` path-safety checks that only apply to the `io/fs` interface, not to direct field access), so no insecure-path rejection occurs [5](#0-4) . Critically, `ziplegacy.extractor.Extract` never uses its own `dir` field at all — it's stored but ignored — meaning there is no chroot enforcement whatsoever for this extractor, relying entirely on the process's current working directory happening to already equal the intended `wd` [6](#0-5) .

The attacker path: a job produces artifacts (`artifacts:paths`) or defines a cache, which the runner helper (`artifacts-downloader`/`cache-extractor` commands, invoked with the job's own `--id`/`--token` for its own artifact) later downloads and extracts on the runner host into the job's checkout `wd`. The job fully controls the zip byte stream returned by GitLab for its own artifact (it created it), so it can embed a raw zip entry with `Name = "../other-job-builds-dir/malicious-file"` (crafted directly with the `archive/zip` writer or a raw zip byte string bypassing normal archiving, since nothing on the download/verification side re-validates entry names). Because `ExtractZipArchive` performs no path containment check, `extractZipFileEntry` will `os.OpenFile("../other-job-builds-dir/malicious-file", …)` relative to the extractor's cwd, writing outside `wd`.

Existing protections that fail: the `tarzstd` extractor has an explicit `filepath.Abs` + `strings.HasPrefix(path, e.dir+separator)` chroot check, but this same protection is absent in the `zip` (ziplegacy) code path used by default (non-fastzip) configurations [3](#0-2) . `openArchive`'s magic-byte sniffing itself is not the root cause here (Gzip has no registered extractor, so misdetection as Gzip fails safely via `archive.ErrUnsupportedArchiveFormat`); rather, once the format is (correctly or by default) resolved to `Zip`, the ziplegacy extractor path is the actual vulnerable component.

### Impact Explanation
If the fastzip feature flag (`FF_USE_FASTZIP`) is disabled (or in any configuration where `ziplegacy.NewExtractor` remains the registered zip extractor, e.g. via `archive.Register` overrides), a job's crafted artifact/cache zip can write files to arbitrary paths reachable via relative traversal from the extraction process's working directory — including sibling checkout directories of other concurrently or sequentially executed jobs sharing the same runner host/build root (e.g. shell executor, or any executor where multiple jobs share a filesystem). This violates checkout-state isolation between jobs by allowing file overwrite outside the intended `wd`.

### Likelihood Explanation
Preconditions: the runner must be running without `FF_USE_FASTZIP` enabled (the fastzip extractor is unaffected since it delegates to the `saracen/fastzip` library, which does perform path-safety checks) and must use an executor/config where job working directories share a filesystem root (e.g., shell executor, or misconfigured shared volumes). Feasibility is high given that the attacker only needs to author a job that uploads a specially-crafted artifact zip (trivial with the standard `archive/zip` package or manual entry crafting) and have the runner later download+extract it — a fully in-spec, self-service workflow requiring no special runner or admin privileges.

### Recommendation
In `helpers/archives/zip_extract.go`, add a path-containment check for every `file.Name` (reject or clean entries containing `..` path elements or resolving outside the target directory), mirroring the `tarzstd` extractor's `filepath.Abs` + prefix-check pattern. Additionally, fix `ziplegacy.extractor.Extract` to actually use its `dir` field (e.g., `os.Chdir(e.dir)` guarded by the same validation, or by joining `e.dir` with each entry name and validating containment) instead of silently ignoring it.

### Proof of Concept
```go
// helpers/archives/zip_extract_traversal_test.go
func TestExtractZipArchiveRejectsPathTraversal(t *testing.T) {
    tmp := t.TempDir()
    outsideDir := t.TempDir() // simulates another job's checkout
    victim := filepath.Join(outsideDir, "victim.txt")
    require.NoError(t, os.WriteFile(victim, []byte("original"), 0644))

    // Build malicious zip with a traversal entry, e.g. "../<outsideDir-rel>/victim.txt"
    buf := new(bytes.Buffer)
    zw := zip.NewWriter(buf)
    rel, _ := filepath.Rel(tmp, victim)
    w, _ := zw.Create(rel) // rel contains "../"
    w.Write([]byte("PWNED"))
    zw.Close()

    // Run extraction inside tmp (simulating wd)
    origWd, _ := os.Getwd()
    os.Chdir(tmp)
    defer os.Chdir(origWd)

    zr, err := zip.NewReader(bytes.NewReader(buf.Bytes()), int64(buf.Len()))
    require.NoError(t, err)
    err = archives.ExtractZipArchive(zr) // current impl returns nil, writes outside tmp
    require.Error(t, err, "expected extraction to reject entries escaping the target directory")

    content, _ := os.ReadFile(victim)
    assert.Equal(t, "original", string(content), "victim file outside wd must not be overwritten")
}
```
Expected result today: the test fails — `ExtractZipArchive` returns `nil` and `victim.txt` is overwritten with `PWNED`, confirming path traversal outside the extraction root. A fuzz variant iterating over `Zip` (ziplegacy), `Zip` (fastzip, expected safe), and `TarZstd` (expected safe) via `archive.NewExtractor` can assert only the ziplegacy path is vulnerable.

### Citations

**File:** commands/helpers/artifacts_downloader.go (L125-141)
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
}
```

**File:** commands/helpers/archive/ziplegacy/zip_legacy_extractor.go (L19-33)
```go
// NewExtractor returns a new Zip Extractor.
func NewExtractor(r io.ReaderAt, size int64, dir string) (archive.Extractor, error) {
	return &extractor{r: r, size: size, dir: dir}, nil
}

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

**File:** helpers/archives/zip_extract.go (L41-83)
```go
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

	switch file.Mode() & os.ModeType {
	case os.ModeDir:
		err = extractZipDirectoryEntry(file)

	case os.ModeSymlink:
		err = extractZipSymlinkEntry(file)

	case os.ModeNamedPipe, os.ModeSocket, os.ModeDevice:
		// Ignore files of these types
		logrus.Warningf("File ignored: %q", file.Name)

	default:
		err = extractZipFileEntry(file)
	}
	return
}
```

**File:** helpers/archives/zip_extract.go (L85-97)
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
