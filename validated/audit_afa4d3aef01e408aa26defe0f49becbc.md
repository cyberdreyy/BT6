### Title
Zip legacy extractor performs no path traversal / symlink-target validation, allowing writes outside the cache extraction directory - ([File: helpers/archives/zip_extract.go])

### Summary
`extractZipFile`/`extractZipSymlinkEntry`/`extractZipFileEntry` operate directly on `file.Name` (and symlink target data) from the zip archive with `os.Mkdir`, `os.OpenFile`, and `os.Symlink`, performing no `filepath.Abs`/`HasPrefix` chroot check like the `tarzstd` extractor does. Since `CacheExtractorCommand.Execute` (in `commands/helpers/cache_extractor.go`) resolves the extraction directory as the process's current working directory and hands the reader straight to `archive.NewExtractor`/`archives.ExtractZipArchive` without any post-extraction confinement check, a malicious cache zip with `../`-relative or absolute entry names, or a symlink entry whose target is absolute/traverses upward, can write or create symlinks outside the intended build/cache directory.

### Finding Description
`ExtractZipArchive` iterates `archive.File` entries and calls `extractZipFile`, which does:

```go
err = os.MkdirAll(filepath.Dir(file.Name), 0o777)
...
case os.ModeSymlink:
    err = extractZipSymlinkEntry(file)
default:
    err = extractZipFileEntry(file)
``` [1](#0-0) 

`extractZipFileEntry` and `extractZipSymlinkEntry` write directly to `file.Name` with no normalization or containment check: [2](#0-1) 

Compare with the `tarzstd` extractor, which explicitly resolves `filepath.Join(e.dir, hdr.Name)` to an absolute path and rejects any entry whose resolved path does not have `e.dir` as a prefix: [3](#0-2) 

The legacy zip extractor (`ziplegacy.extractor.Extract`) is registered for the `Zip`/`ZipZstd` formats and simply forwards to `archives.ExtractZipArchive`, ignoring the `dir` field entirely (it's stored but never used to confine paths): [4](#0-3) 

`CacheExtractorCommand.Execute` computes `wd, err := os.Getwd()` and passes it as `dir` to `archive.NewExtractor(format, f, size, wd)`, but for the legacy zip path that `dir` value is never applied as a containment boundary — extraction happens relative to whatever the process CWD is, and any `../` or absolute entry names in the archive are honored verbatim: [5](#0-4) 

This cache archive is job-controlled: the job specifies cache paths and the archiver (`CacheArchiverCommand`, invoked from the `cache_archive.go` stage) packages files from the job's own workspace into the zip that is later uploaded and, on a subsequent job/run, downloaded and fed unmodified into `cache-extractor` → `ExtractZipArchive`. While the archiver side does sanitize what it writes into the zip (only files under the build dir), an attacker who can directly craft/replace the cache blob content (e.g., via a controlled cache key/bucket, or by directly forging the zip bytes as accepted by any `--file`/URL/GoCloud source) is not otherwise validated by the extractor — the extractor trusts the zip's `file.Name` fields as data, and those are not covered by any allow-list, `HasPrefix`, or resolved-path check.

### Impact Explanation
On a shared runner host (e.g., shell executor, or `run-single`/instance reuse for the same executor), a job that can supply a crafted zip payload as its cache file could, at extraction time, create/overwrite files outside its own `BuildDir`/`CacheDir` — anywhere the runner process's effective filesystem permissions allow, including sibling job workspaces, via `../../` relative entries or absolute entry names (`/etc/...`, `/home/other/...`), and via arbitrary symlink targets. This is a path traversal / arbitrary file write bug in the zip extraction routine itself, structurally identical to a classic "zip slip" vulnerability.

### Likelihood Explanation
The extractor code has zero containment logic (confirmed by direct read of `helpers/archives/zip_extract.go`), so exploitation is straightforward and fully reproducible: any zip with adversarial entry names/symlink targets will be extracted verbatim. Practical exploitation still depends on an attacker being able to substitute or forge the exact bytes of the cache archive that `cache-extractor` downloads (i.e., control over the cache blob content) — normal GitLab cache-key-and-content flow constrains this to the job's own declared `cache:paths`, since the paired `CacheArchiverCommand`/archiver logic controls what legitimate content goes into the zip. The severity of this finding therefore hinges on whether an attacker-controlled path exists to get an arbitrary/malicious zip byte stream accepted as a "cache" download (e.g., cache key collision across projects/branches, or supplying a custom `--url`/`--gocloud-url`) — that specific delivery mechanism was not fully traced in this review; only the extraction-side vulnerability (missing path/symlink containment) was directly confirmed in the code.

### Recommendation
Add the same containment check used by `tarzstd_extractor.go` to `helpers/archives/zip_extract.go`: resolve each `file.Name` against the intended extraction root with `filepath.Abs(filepath.Join(dir, file.Name))`, reject entries whose resolved path does not have `dir+separator` as a prefix (and is not equal to `dir`), and validate symlink targets similarly (either reject absolute/traversing symlink targets outright, or resolve and confine them the same way). This requires plumbing the extraction `dir` into `ExtractZipArchive`/`extractZipFile`, which the current legacy zip code path drops on the floor despite `ziplegacy.extractor` storing a `dir` field.

### Proof of Concept
```go
// helpers/archives/zip_extract_traversal_test.go
func TestExtractZipArchive_PathTraversal(t *testing.T) {
    tmp := t.TempDir()
    root := filepath.Join(tmp, "root")
    require.NoError(t, os.MkdirAll(root, 0755))

    // sibling target outside root that must NOT be written to
    victim := filepath.Join(tmp, "victim.txt")

    var buf bytes.Buffer
    zw := zip.NewWriter(&buf)
    w, _ := zw.Create("../victim.txt")
    _, _ = w.Write([]byte("pwned"))
    zw.Close()

    zr, err := zip.NewReader(bytes.NewReader(buf.Bytes()), int64(buf.Len()))
    require.NoError(t, err)

    wd, _ := os.Getwd()
    require.NoError(t, os.Chdir(root))
    defer os.Chdir(wd)

    _ = archives.ExtractZipArchive(zr)

    // Expected (after fix): victim.txt should NOT exist outside root.
    _, statErr := os.Stat(victim)
    assert.True(t, os.IsNotExist(statErr), "zip entry escaped extraction root: %s", victim)
}
```
Expected result today: the assertion fails because `victim.txt` is created outside `root`, confirming the missing path-containment check in `ExtractZipArchive`/`extractZipFile`.

### Citations

**File:** helpers/archives/zip_extract.go (L22-59)
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

**File:** commands/helpers/archive/ziplegacy/zip_legacy_extractor.go (L12-33)
```go
// extractor is a zip stream extractor.
type extractor struct {
	r    io.ReaderAt
	size int64
	dir  string
}

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

**File:** commands/helpers/cache_extractor.go (L626-663)
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
	if err != nil {
		logrus.Fatalln(err)
	}
```
