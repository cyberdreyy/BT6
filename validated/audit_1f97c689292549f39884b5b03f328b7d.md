This confirms `helpers/archives/zip_extract.go` performs no path containment validation anywhere — `errorIfGitDirectory` only checks for `.git` prefix, and no `filepath.Abs`/`HasPrefix`-style check against a root exists in this file, unlike the sibling `tarzstd` extractor which explicitly validates paths against a chroot (`commands/helpers/archive/tarzstd/tarzstd_extractor.go:58-64`). Since `os.MkdirAll(filepath.Dir(file.Name), 0o777)` runs unconditionally on `file.Name` (a zip-header-controlled string), directory traversal via `../` segments is not prevented before this call.

### Title
Zip extraction lacks path-traversal protection, allowing directory creation outside extraction root - (File: helpers/archives/zip_extract.go)

### Summary
`extractZipFile` calls `os.MkdirAll(filepath.Dir(file.Name), 0o777)` and later `os.OpenFile`/`os.Mkdir`/`os.Symlink` using the raw, attacker-controlled `file.Name` from the zip header, with no path containment check anywhere in this file. This allows a crafted zip (e.g. supplied via `cache-extractor` or artifact extraction) to create directories and write files/symlinks outside the intended extraction root using `../` segments.

### Finding Description
`ExtractZipFile` -> `ExtractZipArchive` -> `extractZipFile` iterates `archive.File` and, for every entry, unconditionally executes `os.MkdirAll(filepath.Dir(file.Name), 0o777)` [1](#0-0)  before dispatching to `extractZipDirectoryEntry`, `extractZipSymlinkEntry`, or `extractZipFileEntry`, all of which operate directly on `file.Name` with no path sanitization [2](#0-1) . The only validation applied anywhere in this file is `errorIfGitDirectory`, which merely detects a leading `.git` path segment and logs a warning without stopping extraction [3](#0-2) . There is no `filepath.Abs`/`strings.HasPrefix` containment check comparing the resolved path against the extraction root, unlike the tar+zstd extractor which explicitly enforces `path` stays under `e.dir` before any `MkdirAll`/file creation [4](#0-3) . The reachable path for job-controlled input is via `CacheExtractorCommand.Execute`, which calls `openArchive`/zip extraction on `c.File` inside the job's working directory `wd` obtained via `os.Getwd()` [5](#0-4) ; a job that controls the cache archive content (e.g. through a pull-then-push cache round trip, or any code path where zip extraction runs on job/pipeline-supplied archive bytes) can embed entries named like `../../victim/newdir/f.txt`.

### Impact Explanation
Because `filepath.Dir("../../victim/newdir/f.txt")` resolves relative to the current working directory, `os.MkdirAll` will create `../../victim/newdir` relative to `wd`, i.e. outside the job's cache/build root, and subsequent `extractZipFileEntry`/`extractZipSymlinkEntry` will write/create a file or symlink there. This matches the scoped impact: directory creation/traversal outside the job root, usable as staging for later cross-project or cross-job filesystem interference on a shared executor filesystem (e.g. shell executor or shared cache volume).

### Likelihood Explanation
The precondition is that an attacker (a normal pipeline author) can influence the zip archive's file-name entries that get extracted through this code path. Zip format entry names are not restricted by the writer/reader used here (`archive/zip` standard library permits arbitrary names including `..` segments), and this file performs zero containment checks, so the exploit is fully reproducible with a hand-crafted zip and does not require any privileged action.

### Recommendation
Add a path-containment check in `extractZipFile` (and ideally in `ExtractZipArchive`) mirroring the tarzstd extractor: resolve `filepath.Join(root, file.Name)` to an absolute path and reject/skip entries whose resolved path is not prefixed by the extraction root before calling `os.MkdirAll`, `os.Mkdir`, `os.OpenFile`, or `os.Symlink`. Also treat entries containing `..` path segments (via `filepath.Clean` component inspection) as invalid input the same way `errorIfGitDirectory` currently flags `.git`.

### Proof of Concept
```go
func TestExtractZipFileDirectoryTraversal(t *testing.T) {
    testOnArchive(t, func(t *testing.T, archive *zip.Writer) {
        f, err := archive.Create("../../victim/newdir/f.txt")
        require.NoError(t, err)
        _, err = io.WriteString(f, "pwn")
        require.NoError(t, err)
    }, func(t *testing.T, fileName string) {
        wd, _ := os.Getwd()
        err := ExtractZipFile(fileName)
        require.NoError(t, err)

        traversalTarget := filepath.Join(wd, "..", "..", "victim", "newdir")
        _, statErr := os.Stat(traversalTarget)
        assert.True(t, os.IsNotExist(statErr), "traversal directory must not be created outside extraction root")
        defer os.RemoveAll(filepath.Join(wd, "..", "..", "victim"))
    })
}
```
Expected (current) result: the test fails because `../../victim/newdir` and `../../victim/newdir/f.txt` are created, proving the missing path-containment check; after applying the recommended fix, the assertion should pass (`os.IsNotExist` true).

### Citations

**File:** helpers/archives/zip_extract.go (L12-59)
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
```

**File:** helpers/archives/zip_extract.go (L61-66)
```go
func extractZipFile(file *zip.File) (err error) {
	// Create all parents to extract the file
	err = os.MkdirAll(filepath.Dir(file.Name), 0o777)
	if err != nil {
		return err
	}
```

**File:** helpers/archives/path_check_helper.go (L13-31)
```go
func isPathAGitDirectory(path string) bool {
	parts := strings.Split(filepath.Clean(path), string(filepath.Separator))
	if len(parts) > 0 && parts[0] == ".git" {
		return true
	}
	return false
}

func errorIfGitDirectory(path string) *os.PathError {
	if !isPathAGitDirectory(path) {
		return nil
	}

	return &os.PathError{
		Op:   ".git inside of archive",
		Path: path,
		Err:  errors.New("trying to archive or extract .git path"),
	}
}
```

**File:** commands/helpers/archive/tarzstd/tarzstd_extractor.go (L57-68)
```go
		var path string
		path, err = filepath.Abs(filepath.Join(e.dir, hdr.Name))
		if err != nil {
			return err
		}
		if !strings.HasPrefix(path, e.dir+string(filepath.Separator)) && path != e.dir {
			return fmt.Errorf("%s cannot be extracted outside of chroot (%s)", path, e.dir)
		}

		if err := os.MkdirAll(filepath.Dir(path), 0777); err != nil {
			return err
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
