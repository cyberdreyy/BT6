### Title
Legacy zip extractor performs no path-traversal / chroot validation on entry names — ([File: helpers/archives/zip_extract.go])

### Summary
`archives.ExtractZipArchive` / `extractZipFile` (used by `ziplegacy.NewExtractor`, the default `Zip`/`ZipZstd` extractor when `FF_USE_FASTZIP` is not enabled) writes each `zip.File.Name` directly to disk with no join-and-verify against an extraction root, unlike the `tarzstd` and `fastzip` extractors which explicitly check the resolved path is inside `dir`. This breaks the documented invariant that extraction must be symmetric to the archiver's "children of the directory" guarantee.

### Finding Description
`CacheExtractorCommand.Execute` and `ArtifactsDownloaderCommand.Execute` call `archive.NewExtractor(format, f, size, wd)` and then `extractor.Extract(ctx)`. [1](#0-0) [2](#0-1) 

For `archive.Zip`/`archive.ZipZstd`, the registered extractor is `ziplegacy.NewExtractor` unless the `FF_USE_FASTZIP` feature flag is enabled, in which case `fastzip.NewExtractor` overrides the registration: [3](#0-2) [4](#0-3) 

`ziplegacy.extractor.Extract` ignores the `dir` field entirely and calls `archives.ExtractZipArchive(zr)` with no target-directory argument at all — it relies on the caller having already `chdir`'d into the correct directory (confirmed by the "hack: legacy archiver require being in the correct working dir" comment in the test suite): [5](#0-4) [6](#0-5) 

Inside `helpers/archives/zip_extract.go`, `extractZipFile`/`extractZipFileEntry`/`extractZipSymlinkEntry`/`extractZipDirectoryEntry` use `file.Name` (the raw, attacker-supplied zip entry name) directly as a filesystem path passed to `os.MkdirAll`, `os.OpenFile`, `os.Mkdir`, and `os.Symlink` — there is no `filepath.Clean`, no absolute-path rejection, and no "must remain under root" check: [7](#0-6) 

This is in stark contrast to the `tarzstd` extractor (and `fastzip`, via the underlying `saracen/fastzip` library), which explicitly resolves the absolute path and rejects any entry that escapes the chroot: [8](#0-7) 

So a zip entry with a name like `../../../home/gitlab-runner/.ssh/authorized_keys` or an absolute path `/etc/cron.d/x` extracted through the legacy zip path will be written exactly where the attacker names it (subject only to OS filesystem permissions of the runner process), not confined to the job's working directory. The only existing "protections" in `ExtractZipArchive` are a `.git`-directory warning (`errorIfGitDirectory`) and metadata processing — neither validates path containment.

### Impact Explanation
Where the legacy zip extractor is used to unpack a cache or artifact archive whose bytes/entry names an attacker can influence, this allows writing files to arbitrary paths reachable by the runner helper process — outside the job workspace root — including potential persistence (e.g. dropping files onto a shared runner filesystem that outlive the job and are read by future jobs, or in the worst case under shell/host-mounted executors, arbitrary filesystem write with the runner's privileges). This exactly matches the scoped impact: unauthorized file write outside job workspace root with cross-job persistence risk.

### Likelihood Explanation
Reaching the vulnerable code path requires (a) that `FF_USE_FASTZIP` not be active for the job — this flag defaults to being settable, and GitLab Runner feature flags are configurable per-job via CI/CD variables (e.g. `variables: FF_USE_FASTZIP: "false"` in `.gitlab-ci.yml`), which is an unprivileged, job-author-controlled input; and (b) the ability to influence the actual zip byte content fetched by `CacheExtractorCommand`/`ArtifactsDownloaderCommand`. The audit's stated precondition grants (b) directly ("attacker can produce or influence archive bytes fetched by cache-extractor/artifact-downloader for their own job"); note that the normal `CacheArchiverCommand` path (which walks real files under the job's own directory) would not naturally emit `../`-style entries, so a concrete end-to-end PoC via the stock `cache:paths:` mechanism was not verified in this audit — it would require an alternate delivery path (e.g. a custom/GoCloud cache URL or a pre-existing malicious local cache file) for the crafted bytes to reach the extractor. This is a real code-level gap (missing containment check) rather than a purely theoretical one, but full exploitability depends on details of how cache/artifact bytes for a given job can be substituted, which could not be fully confirmed from the indexed code alone.

### Recommendation
In `helpers/archives/zip_extract.go`, require callers to pass the target directory explicitly and validate every `file.Name` the same way `tarzstd`/`fastzip` do: resolve `filepath.Join(dir, file.Name)`, take `filepath.Abs`, and reject the entry (or the whole archive) if the resolved path is not equal to or a child of `dir`, symmetric to the `NewArchiver` guarantee. Also reject absolute paths and Windows drive-letter paths in `file.Name` before any join. Have `ziplegacy.extractor.Extract` pass `e.dir` into `ExtractZipArchive`/`extractZipFile` instead of relying on the process's current working directory.

### Proof of Concept
```go
// helpers/archives/zip_extract_test.go
func TestExtractZipFile_PathTraversal(t *testing.T) {
    outsideDir := t.TempDir()
    root := t.TempDir()
    chdir(t, root) // ziplegacy relies on cwd == extraction root

    tempFile, _ := os.CreateTemp("", "evil.zip")
    zw := zip.NewWriter(tempFile)
    w, _ := zw.Create(filepath.Join("../../..", filepath.Base(outsideDir), "pwned.txt"))
    w.Write([]byte("owned"))
    zw.Close()
    tempFile.Close()

    err := ExtractZipFile(tempFile.Name())
    require.NoError(t, err) // currently succeeds — should instead error

    _, statErr := os.Stat(filepath.Join(outsideDir, "pwned.txt"))
    assert.True(t, os.IsNotExist(statErr), "file must not be written outside extraction root")
}
```
Expected (fixed) behavior: `ExtractZipFile`/`ExtractZipArchive` returns an error such as `"pwned.txt cannot be extracted outside of chroot"` and no file is created outside `root`, matching the behavior already enforced by the `tarzstd` extractor's chroot check.

### Citations

**File:** commands/helpers/cache_extractor.go (L655-663)
```go
	extractor, err := archive.NewExtractor(format, f, size, wd)
	if err != nil {
		logrus.Fatalln(err)
	}

	err = extractor.Extract(context.Background())
	if err != nil {
		logrus.Fatalln(err)
	}
```

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

**File:** commands/helpers/archiver.go (L19-37)
```go
func init() {
	// enable fastzip archiver/extractor
	logger := logrus.WithField("name", featureflags.UseFastzip)
	if on := featureflags.IsOn(logger, os.Getenv(featureflags.UseFastzip)); on {
		archive.Register(archive.Zip, fastzip.NewArchiver, fastzip.NewExtractor)

		// The default zstd compressor is fastzip, this is registered via the
		// fastzip implementation (helpers/archive/fastzip).
		//
		// The default zstd decompressor is the legacy zip implementation (helpers/archive/ziplegacy).
		// This intended to allow the default zip implementation to still be able to decompress zstd,
		// even if it is unable to compress it (only fastzip can compress). This also allows the older
		// extraction behaviour to be enabled.
		//
		// Here we're registering the decompress only if FF_USE_FASTZIP is enabled. This overrides
		// the ziplegacy zstd support.
		archive.Register(archive.ZipZstd, nil, fastzip.NewExtractor)
	}
}
```

**File:** commands/helpers/archive/ziplegacy/zip_legacy_archiver.go (L16-21)
```go
func init() {
	zip.RegisterDecompressor(zstd.ZipMethodWinZip, fastzip.ZstdDecompressor())

	archive.Register(archive.Zip, NewArchiver, NewExtractor)
	archive.Register(archive.ZipZstd, nil, NewExtractor)
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

**File:** commands/helpers/archiver_test.go (L66-76)
```go
		out := t.TempDir()

		// hack: legacy archiver require being in the correct working dir
		_ = os.Chdir(out)

		// for Windows: change directory on exit so that we're not "using" the directory we're removing
		defer func() { _ = os.Chdir(originalDir) }()

		extractor, err := archive.NewExtractor(format, bytes.NewReader(input), int64(len(input)), out)
		require.NoError(t, err)
		require.NoError(t, extractor.Extract(t.Context()))
```

**File:** helpers/archives/zip_extract.go (L12-83)
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
