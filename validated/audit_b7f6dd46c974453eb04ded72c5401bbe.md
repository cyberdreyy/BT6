### Title
Path traversal in legacy zip extractor allows writing/deleting files outside extraction directory - (File: helpers/archives/zip_extract.go)

### Summary
`extractZipFileEntry` and `extractZipDirectoryEntry` (and `extractZipSymlinkEntry`) operate directly on `file.Name` from the zip archive with no containment check, unlike the sibling `fastzip`/`tarzstd` extractors which validate the resolved path stays under the target directory. The `os.Remove(file.Name)` call preceding creation is part of a broader zip-slip: if `file.Name` contains `../` segments, both the `Remove` and the subsequent `OpenFile(...O_CREATE|O_TRUNC...)`/`Mkdir` operate on a path outside the intended extraction root.

### Finding Description
`ExtractZipArchive` iterates `archive.File` entries and calls `extractZipFile(file)` for each, which dispatches to `extractZipDirectoryEntry`, `extractZipSymlinkEntry`, or `extractZipFileEntry` based on entry type [1](#0-0) . None of these functions sanitize or validate `file.Name` against path traversal, unlike the `tarzstd` extractor, which explicitly resolves the path with `filepath.Abs(filepath.Join(e.dir, hdr.Name))` and rejects any path that escapes the target directory before performing any filesystem mutation [2](#0-1) .

Critically, the legacy zip extractor (`ziplegacy.extractor.Extract`) doesn't even join `file.Name` with the target `dir` passed to `NewExtractor` — it ignores `dir` entirely and calls `archives.ExtractZipArchive(zr)` directly, meaning entry names are resolved relative to the process's current working directory [3](#0-2) . Both `cache-extractor` and `artifacts-downloader` commands set `wd, _ := os.Getwd()` and pass it into `archive.NewExtractor`, but since the legacy zip path discards `dir`, a crafted entry name like `../../some/file` in a cache/artifact zip is resolved directly against the job's working directory, with `os.Remove(file.Name)` and `os.OpenFile(file.Name, os.O_CREATE|os.O_TRUNC...)` executing on that resolved path [4](#0-3) [5](#0-4) [6](#0-5) .

The only existing guard, `errorIfGitDirectory`, only blocks paths starting with `.git`; it does not check for `..` traversal at all [7](#0-6) [8](#0-7) .

### Impact Explanation
An attacker who controls the content of a cache or artifact zip archive extracted through this legacy code path can delete or overwrite arbitrary files reachable via relative traversal from the job's working directory (or from the extraction root in other callers), destroying or corrupting files outside the intended job workspace. This matches the scoped impact: destructive side effects beyond the job workspace, distinct from and worse than the normal in-workspace overwrite behavior, because there is no boundary enforcement at all for this extractor.

### Likelihood Explanation
Feasibility depends on whether the legacy zip extractor path (`archives.ExtractZipArchive` via `ziplegacy`) is actually reachable for attacker-controlled cache/artifact content in the deployed configuration versus the `fastzip` extractor (which is presumably the default and does not exhibit this exact issue, since `fastzip` is a separate implementation not reviewed here). I was not able to confirm from the available index which extractor (`fastzip` vs `ziplegacy`) is selected by default for a given archive/format, since the extractor registration table in `commands/helpers/archive/archive.go` and the format-selection logic in `openArchive` were not fully visible. If `ziplegacy` is reachable (e.g., as a fallback path, via feature flag, or for a specific format signature), the exploit is trivially repeatable: any pipeline author who can influence cache/artifact zip contents can trigger it. Confirming this requires checking `commands/helpers/archive/archive.go`'s extractor registration and `openArchive`'s format-detection logic, which I could not fully retrieve.

### Recommendation
Add the same path-containment validation used in `tarzstd_extractor.go` to `helpers/archives/zip_extract.go`: for every `file.Name`, resolve it against the target directory with `filepath.Abs(filepath.Join(dir, file.Name))` and reject entries whose resolved path does not have `dir` as a prefix, before calling `os.Remove`, `os.OpenFile`, `os.Mkdir`, or `os.Symlink`. Additionally, fix `ziplegacy.extractor.Extract` to actually use its `dir` field (currently ignored) so extraction is properly scoped.

### Proof of Concept
```go
func TestExtractZipArchive_PathTraversalDeletesOutsideFile(t *testing.T) {
    outsideDir := t.TempDir()
    sentinel := filepath.Join(outsideDir, "sentinel.txt")
    require.NoError(t, os.WriteFile(sentinel, []byte("do-not-touch"), 0o644))

    extractDir := filepath.Join(outsideDir, "job-workspace")
    require.NoError(t, os.MkdirAll(extractDir, 0o755))

    // relative traversal from extractDir back to sentinel.txt
    rel, _ := filepath.Rel(extractDir, sentinel)

    buf := new(bytes.Buffer)
    zw := zip.NewWriter(buf)
    w, _ := zw.Create(rel) // e.g. "../sentinel.txt"
    _, _ = w.Write([]byte("attacker-controlled"))
    require.NoError(t, zw.Close())

    origWD, _ := os.Getwd()
    defer os.Chdir(origWD)
    require.NoError(t, os.Chdir(extractDir))

    zr, err := zip.NewReader(bytes.NewReader(buf.Bytes()), int64(buf.Len()))
    require.NoError(t, err)
    require.NoError(t, archives.ExtractZipArchive(zr))

    content, err := os.ReadFile(sentinel)
    require.NoError(t, err)
    // Expected (pre-fix, bug reproduced): content == "attacker-controlled" i.e. sentinel was overwritten
    assert.NotEqual(t, "do-not-touch", string(content))
}
```
Expected assertion after fix: `ExtractZipArchive` returns an error/warning for the `../sentinel.txt` entry and `sentinel.txt` content remains `"do-not-touch"`.

### Citations

**File:** helpers/archives/zip_extract.go (L41-59)
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
```

**File:** helpers/archives/zip_extract.go (L61-83)
```go
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

**File:** helpers/archives/zip_extract.go (L88-91)
```go
	for _, file := range archive.File {
		if err := errorIfGitDirectory(file.Name); tracker.actionable(err) {
			printGitArchiveWarning("extract")
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

**File:** commands/helpers/archive/ziplegacy/zip_legacy_extractor.go (L24-32)
```go
// Extract extracts files from the reader to the directory passed to
// NewZipExtractor.
func (e *extractor) Extract(ctx context.Context) error {
	zr, err := zip.NewReader(e.r, e.size)
	if err != nil {
		return err
	}

	return archives.ExtractZipArchive(zr)
```

**File:** commands/helpers/cache_extractor.go (L626-660)
```go
	wd, err := os.Getwd()
	if err != nil {
		logrus.Fatalln("Unable to get working directory")
	}

	if c.File == "" {
		warningln("Missing cache file")
	}

	if c.URL != "" || c.GoCloudURL != "" {
		err := c.doRetry(c.download)
		if err != nil {
			warningln(err)
		}
	} else {
		logrus.Infoln(
			"No URL provided, cache will not be downloaded from shared cache server. " +
				"Instead a local version of cache will be extracted.")
	}

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
```

**File:** commands/helpers/artifacts_downloader.go (L88-140)
```go
func (c *ArtifactsDownloaderCommand) Execute(cliContext *cli.Context) {
	log.SetRunnerFormatter()

	wd, err := os.Getwd()
	if err != nil {
		logrus.Fatalln("Unable to get working directory")
	}

	if c.URL == "" {
		logrus.Warningln("Missing URL (--url)")
	}
	if c.Token == "" {
		logrus.Warningln("Missing runner credentials (--token)")
	}
	if c.ID <= 0 {
		logrus.Warningln("Missing build ID (--id)")
	}
	if c.ID <= 0 || c.Token == "" || c.URL == "" {
		logrus.Fatalln("Incomplete arguments")
	}

	// Create temporary file
	file, err := os.CreateTemp(c.StagingDir, "artifacts")
	if err != nil {
		logrus.Fatalln(err)
	}
	_ = file.Close()
	defer func() { _ = os.Remove(file.Name()) }()

	// Download artifacts file
	err = c.doRetry(func(retry int) error {
		return c.download(file.Name(), retry)
	})
	if err != nil {
		logrus.Fatalln(err)
	}

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

**File:** helpers/archives/path_check_helper.go (L13-19)
```go
func isPathAGitDirectory(path string) bool {
	parts := strings.Split(filepath.Clean(path), string(filepath.Separator))
	if len(parts) > 0 && parts[0] == ".git" {
		return true
	}
	return false
}
```
