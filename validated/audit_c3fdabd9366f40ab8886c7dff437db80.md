### Title
Path traversal in legacy zip extractor allows writing files outside job working directory - (`helpers/archives/zip_extract.go`)

### Summary
The default zip extraction path (`ziplegacy.NewExtractor` → `archives.ExtractZipArchive` → `extractZipFile`/`extractZipSymlinkEntry`) writes files using the raw `zip.File.Name` from the archive with no validation that the resolved path stays within the intended extraction directory. Unlike the `tarzstd` extractor, which resolves each entry against `e.dir` and rejects paths escaping it (`chroot`-style check), the zip extractor never even uses the `dir` parameter it is given.

### Finding Description
`commands/helpers/archive/ziplegacy/zip_legacy_extractor.go`'s `(*extractor).Extract` receives `dir` (the job working directory) in `NewExtractor(r, size, dir)`, stores it on the struct, but never passes or uses it — it directly calls `archives.ExtractZipArchive(zr)`: [1](#0-0) 

`ExtractZipArchive` then iterates every `zip.File` and calls `extractZipFile(file)` using `file.Name` verbatim: [2](#0-1) 

`extractZipFile` does `os.MkdirAll(filepath.Dir(file.Name), 0o777)` and then, depending on entry type, `extractZipFileEntry`/`extractZipSymlinkEntry`/`extractZipDirectoryEntry` call `os.OpenFile(file.Name, ...)`, `os.Symlink(data, file.Name)`, or `os.Mkdir(file.Name, ...)` directly on `file.Name`: [3](#0-2) 

None of these functions resolve `file.Name` against a base directory nor check for `..` traversal or absolute paths. This contrasts with `commands/helpers/archive/tarzstd/tarzstd_extractor.go`, which explicitly joins each entry with `e.dir`, converts to an absolute path, and rejects the entry if it doesn't stay within `e.dir`: [4](#0-3) 

**Reachability**: `archive.Zip` is registered by default to `ziplegacy.NewExtractor` unless `FF_USE_FASTZIP` is enabled (in which case `fastzip.NewExtractor` is used instead — that implementation was not inspected here but is a separate code path): [5](#0-4) 

Both `ArtifactsDownloaderCommand.Execute` and `CacheExtractorCommand.Execute` call `openArchive` to sniff the format (defaulting to `archive.Zip` unless zstd/gzip magic bytes are found) and then `archive.NewExtractor(format, f, size, wd)`, feeding attacker-controlled archive bytes into this path with `wd` as the job working directory: [6](#0-5) [7](#0-6) 

An unprivileged pipeline author fully controls the contents of artifacts and caches produced by their own job stages (via `artifacts:paths`/`cache:paths` and arbitrary shell/script commands in earlier stages, or by crafting a zip directly and placing it where the runner will download it as an artifact via `dependencies:`). Since the archive is created and later re-consumed within the same project's pipeline machinery, a job can produce a zip whose `zip.File.Name` fields contain sequences like `../../../etc/cron.d/x` or an absolute path such as `/root/.ssh/authorized_keys`, or a symlink entry pointing outside the workspace whose target is then followed for later writes.

### Impact Explanation
When such a zip is extracted by `ExtractZipArchive` during artifact/cache restore (`artifacts-downloader` or cache-extractor helper invoked by the executor, including inside Docker helper containers that mount the shared build volume), files are written to arbitrary paths outside the intended job working directory that the runner process/helper container's user has permission to write to. On shared-volume executors (e.g. Docker executor with `builds_dir` bind-mounted, or shell executor build directories), this allows the job to write to sibling job directories or other paths on that volume it shouldn't otherwise reach, i.e. path traversal / arbitrary file write outside the job workspace confined by other extractors (tarzstd/fastzip is unverified here).

### Likelihood Explanation
High feasibility: no special privileges are required beyond authoring a normal CI job. The attacker only needs to produce a zip artifact/cache (trivially done from a `script:` step using standard zip tooling, or a custom binary) with crafted entry names, then have a later stage/job in the same pipeline (or another job with `dependencies:` on it) trigger extraction via the normal artifact/cache restore flow. This is fully repeatable and requires only default runner configuration (zip is the default cache/artifact compression format; `FF_USE_FASTZIP` is off by default, keeping the vulnerable `ziplegacy` extractor active).

### Recommendation
In `helpers/archives/zip_extract.go`, thread the target extraction directory (`dir`) through `ExtractZipArchive`/`extractZipFile`/`extractZipSymlinkEntry`/`extractZipDirectoryEntry`, resolve `file.Name` against that directory with `filepath.Join` + `filepath.Abs`, and reject any entry whose resolved path is not a descendant of `dir` (or equal to it), mirroring the chroot check already implemented in `commands/helpers/archive/tarzstd/tarzstd_extractor.go` (lines 57-64). Also validate symlink targets aren't used to redirect subsequent writes outside the tree. Update `ziplegacy.NewExtractor`'s `Extract` method to actually pass `e.dir` into `ExtractZipArchive`.

### Proof of Concept
```go
package archives

import (
    "archive/zip"
    "bytes"
    "os"
    "path/filepath"
    "testing"
)

func TestExtractZipArchive_PathTraversal(t *testing.T) {
    dir := t.TempDir()
    workDir := filepath.Join(dir, "work")
    os.MkdirAll(workDir, 0o755)

    outside := filepath.Join(dir, "pwned")

    buf := new(bytes.Buffer)
    zw := zip.NewWriter(buf)
    // relative traversal escaping workDir
    f, _ := zw.Create("../pwned/evil.txt")
    f.Write([]byte("owned"))
    zw.Close()

    zr, _ := zip.NewReader(bytes.NewReader(buf.Bytes()), int64(buf.Len()))

    cwd, _ := os.Getwd()
    os.Chdir(workDir)
    defer os.Chdir(cwd)

    err := ExtractZipArchive(zr)
    if err != nil {
        t.Fatalf("unexpected error: %v", err)
    }

    if _, statErr := os.Stat(filepath.Join(outside, "evil.txt")); statErr != nil {
        t.Fatalf("expected traversal write to succeed outside workDir, got: %v", statErr)
    }
}
```
Expected (current, vulnerable) behavior: the file is created at `outside/evil.txt`, outside `workDir`, proving the missing confinement check. After applying the recommended fix (chroot-style validation like `tarzstd_extractor.go`), `ExtractZipArchive` should instead return an error such as `"... cannot be extracted outside of chroot ..."` and the file must not appear outside `workDir`.

### Citations

**File:** commands/helpers/archive/ziplegacy/zip_legacy_extractor.go (L12-32)
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
```

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

**File:** commands/helpers/cache_extractor.go (L646-664)
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
}
```
