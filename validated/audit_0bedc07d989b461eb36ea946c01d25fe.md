### Title
Zip extraction path traversal / zip-slip in `ziplegacy` extractor lacks containment check present in `tarzstd` extractor - (File: helpers/archives/zip_extract.go)

### Summary
`ziplegacy.extractor.Extract` (`commands/helpers/archive/ziplegacy/zip_legacy_extractor.go`) delegates directly to `archives.ExtractZipArchive`, which calls `extractZipFile` on every zip entry using the raw `file.Name` from the archive with no path containment check, no absolute-path rejection, and no `..` traversal rejection. This is a real, exploitable difference from the sibling `tarzstd` extractor, which explicitly enforces that every extracted path stays under the target `dir`.

### Finding Description
`ExtractZipArchive` iterates over `archive.File` entries and, for each, only checks whether the entry looks like a `.git` path (`errorIfGitDirectory`, a warning-only check based on the first path segment being `.git`) before calling `extractZipFile`: [1](#0-0) 

`extractZipFile` then creates parent directories and writes the file/symlink/directory at `file.Name` verbatim, with no `filepath.Abs`/`filepath.Clean`/prefix check against the destination directory at all: [2](#0-1) 

This is asymmetric with the `tarzstd` extractor, which resolves each header name against the destination directory and explicitly rejects anything that escapes it: [3](#0-2) 

The zip legacy extractor performs none of this validation before calling into `archives.ExtractZipArchive`: [4](#0-3) 

Symlink entries are also extracted without validating the link target or the entry name, via `extractZipSymlinkEntry`, meaning a two-step attack (plant a symlink pointing outside the build root, then a second entry writing "through" that symlink name) is also possible since neither the symlink target nor subsequent entry names are checked for escape: [5](#0-4) 

Reachability: `archive.Extractor` for `zip`/`zipzstd` format is selected by `archive.NewExtractor`, invoked from both artifact download (`commands/helpers/artifacts_downloader.go`) and cache extraction (`commands/helpers/cache_extractor.go`), both of which extract into the job's working directory (`wd`) using attacker/pipeline-controlled archive bytes (an artifact or cache blob): [6](#0-5) [7](#0-6) 

The `ziplegacy` extractor is a registered, selectable, actively-tested extractor implementation (used as the "legacy" fallback path alongside `fastzip`), not dead code: [8](#0-7) 

The only "filter" applied on entry names anywhere in this path is the `.git`-directory heuristic (`isPathAGitDirectory`), which only inspects the first path segment after `filepath.Clean` and is a warning-only advisory check (`tracker.actionable` logs and continues, it does not abort extraction): [9](#0-8) 

There is no equivalent of the "restore filter" that treats hidden/dotfile/absolute/`..`/link entries consistently — the check is name-prefix based and can be trivially bypassed (e.g. `../secret`, `/etc/foo`, `a/../../secret`, or any name not literally starting with `.git`), and no containment enforcement exists at all in this file.

### Impact Explanation
An attacker who controls the content of an artifact or cache archive consumed by a job (e.g., an artifact produced by a prior stage/job in the same pipeline, or a cache blob written by an earlier job and restored by a later job/executor) can craft zip entries with `../` sequences or absolute paths to write files outside the intended build directory. Since the extractor runs with the job's execution privileges, this can overwrite files outside the build root the executor otherwise trusts (e.g. trusted config files reachable by the executor user, or other locations within the shared filesystem), leading to secret exposure or trusted-config overwrite, matching the scoped impact.

### Likelihood Explanation
Precondition: content of the zip archive being extracted (artifact or cache) must be attacker-influenced, which is realistic for a pipeline author controlling job outputs, or for any job that consumes artifacts from an untrusted/attacker-controlled prior stage. The `ziplegacy` extractor is a supported, registered code path exercised by tests as a legacy/fallback zip extractor, so it is reachable in real deployments (not merely dead code). The exploit requires no special executor misconfiguration — it only depends on the missing path-containment check in `extractZipFile`, making it straightforward and repeatable to reproduce with a hand-crafted zip file.

### Recommendation
Add the same containment check used in `tarzstd_extractor.go` to `helpers/archives/zip_extract.go`/`ExtractZipArchive`: resolve each `file.Name` against the destination directory with `filepath.Abs`/`filepath.Join`, reject entries whose resolved path is not a descendant of (or equal to) the destination directory, and reject/neutralize symlink entries whose link target escapes the destination directory. Apply this uniformly regardless of dotfile/hidden naming so no entry name variant bypasses the check.

### Proof of Concept
Go unit test in `helpers/archives` (or `commands/helpers/archive/ziplegacy`) package:
1. Build an in-memory `zip.Writer` with an entry named `../outside.txt` (and a variant `/tmp/outside.txt` or `..\\..\\outside.txt` on Windows) containing marker content.
2. Call `ziplegacy.NewExtractor(reader, size, tmpDestDir)` then `Extract(ctx)`.
3. Assert: extraction should fail or the marker file must not exist anywhere outside `tmpDestDir` (e.g., `assert.NoFileExists(t, filepath.Join(filepath.Dir(tmpDestDir), "outside.txt"))`); currently the test will show the file is written outside `tmpDestDir`, proving the vulnerability.
4. Repeat with a symlink entry (`os.ModeSymlink`) whose target is `../../` followed by a second entry using that symlink's name as a directory prefix, asserting no write occurs outside `tmpDestDir`.

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

**File:** commands/helpers/helpers_archiver_test.go (L58-76)
```go
func OnEachZipExtractor(t *testing.T, f func(t *testing.T), include ...string) {
	extractors := map[string]archive.NewExtractorFunc{
		"legacy":  ziplegacy.NewExtractor,
		"fastzip": fastzip.NewExtractor,
	}

	for name, extractor := range extractors {
		if !hasArchiver(name, include) {
			continue
		}
		t.Run(name, func(t *testing.T) {
			prevArchiver, prevExtractor := archive.Register(archive.Zip, ziplegacy.NewArchiver, extractor)
			t.Cleanup(func() {
				archive.Register(archive.Zip, prevArchiver, prevExtractor)
			})
			f(t)
		})
	}
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
