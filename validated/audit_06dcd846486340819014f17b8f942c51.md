### Title
Zip-slip path traversal in `ExtractZipArchive` allows writing files and chowning them outside the job's working directory - (File: helpers/archives/zip_extract.go, helpers/archives/zip_extra_unix.go)

### Summary
`ExtractZipArchive` and its helpers (`extractZipFile`/`extractZipFileEntry` in `helpers/archives/zip_extract.go`) use `file.Name` from the zip's `FileHeader` directly in `os.MkdirAll`, `os.OpenFile`, `os.Symlink`, and `os.Mkdir` calls with no path-traversal sanitization. The only content check performed, `errorIfGitDirectory`, only rejects `.git`-prefixed paths and does nothing for `../` segments, so a crafted cache/artifact archive can write files anywhere the runner process can reach, and subsequently `processZipExtra` → `processZipUIDGidField` will `os.Lchown` that same attacker-controlled path.

### Finding Description
`ExtractZipArchive` iterates `archive.File` and for each entry calls `extractZipFile(file)`: [1](#0-0) 

`extractZipFile` computes `filepath.Dir(file.Name)`, calls `os.MkdirAll` on it, then dispatches to `extractZipFileEntry` for regular files, which does `os.OpenFile(file.Name, os.O_WRONLY|os.O_CREATE|os.O_TRUNC, ...)`: [2](#0-1) 

There is no call to `filepath.Clean`, no rejection of `..` segments, and no check that the resolved path stays under the working directory. The only guard applied to `file.Name` is `errorIfGitDirectory`, which is a `strings.Split` check for a leading `.git` component and is unrelated to path traversal: [3](#0-2) 

After all files are written, the second loop calls `lchmod(file.Name, ...)` and `processZipExtra(&file.FileHeader)`: [4](#0-3) 

`processZipExtra` parses the zip "extra" field and, for a `ZipUIDGidFieldType` (`0x7875`) record, calls `processZipUIDGidField`, which performs `os.Lchown(file.Name, int(ugField.UID), int(ugField.Gid))` using the same unsanitized `file.Name`: [5](#0-4) [6](#0-5) 

Both the write path and the chown path operate on attacker-supplied UID/GID and attacker-supplied file names, with zero path validation anywhere in this file.

The extraction is invoked, unchanged, from `CacheExtractorCommand.Execute`, which downloads/opens the cache archive and calls `archive.NewExtractor(...).Extract(ctx)` in the job's working directory (`wd`): [7](#0-6) 

Since a CI job fully controls the contents of the cache/artifact archive it uploads (via `CacheArchiverCommand` archiving job-controlled files, or a crafted job simply producing an arbitrary zip and presenting it as a "cache"/"artifact" payload consumed later by `CacheExtractorCommand`/`ArtifactsDownloaderCommand`), an attacker can set `FileHeader.Name` to `../../other-project/.git/config` (or any other traversal target reachable from the extraction `wd`) and attach a `ZipUIDGidField` extra record. There is no existing check (allowed paths, symlink target validation, `.git`-only filter, or zip-slip guard) that stops this.

### Impact Explanation
A victim job that downloads and extracts an attacker-supplied cache/artifact zip will have arbitrary files written (and their symlink targets/timestamps set) at attacker-chosen relative paths outside the intended cache/artifact root, and the same paths' ownership changed via `os.Lchown` to attacker-chosen UID/GID — bounded only by filesystem permissions of the runner process user. This can corrupt or replace files in sibling checkouts/working directories on a shared host/executor (e.g. another concurrently-checked-out project's files, if reachable via relative traversal from the extraction working directory), and can flip ownership of files the runner process has access to, which is a concrete violation of the "file operations must stay within the intended cache/artifact root" invariant.

### Likelihood Explanation
The attacker only needs to control the contents of a cache or artifact archive that they push, which is fully within a normal, unprivileged CI job's capability (job scripts define what gets cached/archived, and low-level control of a raw zip's `FileHeader.Name`/extra fields is achievable by crafting the zip directly rather than relying on the `zip` package's own path handling during creation). The trigger requires a second job (any job, potentially the attacker's own job re-run, or a shared runner reusing the same host/working-directory family) to download and extract that cache/artifact — this is normal, automatic runner behavior via `CacheExtractorCommand`/`ArtifactsDownloaderCommand`, with no additional victim interaction. No existing check in `helpers/archives` (only `errorIfGitDirectory`) would block it, making this reliably reproducible.

### Recommendation
In `helpers/archives/zip_extract.go`, before performing any filesystem operation on `file.Name`, validate that the cleaned path is relative and does not escape the destination root (e.g., reject entries where `filepath.Clean(file.Name)` starts with `../` or is absolute, or resolve `filepath.Join(destRoot, file.Name)` and verify it remains under `destRoot` via `filepath.Rel`/prefix check). Apply the same validation before `processZipExtra`/`lchmod` operate on `file.Name` in the second loop, and consider applying an equivalent guard to any other archive format extractors (tar, etc.) sharing this pattern.

### Proof of Concept
Add a unit test in `helpers/archives/zip_extract_test.go`:
1. Build an in-memory `zip.Writer` writing to a `bytes.Buffer` with one entry whose `FileHeader.Name = "../outside/pwned.txt"`, mode = regular file, content = arbitrary bytes, and `Extra` set to a valid `ZipExtraField{Type: ZipUIDGidFieldType, Size: ...}` + `ZipUIDGidField{Version:1, UIDSize:4, UID: <attacker-uid>, GIDSize:4, Gid: <attacker-gid>}` encoded via `binary.Write`.
2. `os.MkdirTemp` two sibling directories, `victimRoot` and `victimRoot/../outside` (i.e., a parent `outside` dir next to `victimRoot`); `os.Chdir(victimRoot)`.
3. Open the buffer as `*zip.Reader` via `zip.NewReader` and call `archives.ExtractZipArchive(reader)`.
4. Assert `outside/pwned.txt` exists at the sibling path (proving traversal escape) and, on unix, assert its owner/group differs from the process default (proving `Lchown` was applied outside the intended root) — expected (buggy) result: file exists outside `victimRoot`; fixed behavior: `ExtractZipArchive` returns/logs an error and no file is created outside `victimRoot`.

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

**File:** helpers/archives/zip_extract.go (L85-107)
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

	for _, file := range archive.File {
		if err := lchmod(file.Name, file.Mode()); tracker.actionable(err) {
			logrus.Warningf("%s: %s (suppressing repeats)", file.Name, err)
		}

		// Process zip metadata
		if err := processZipExtra(&file.FileHeader); tracker.actionable(err) {
			logrus.Warningf("%s: %s (suppressing repeats)", file.Name, err)
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

**File:** helpers/archives/zip_extra_unix.go (L37-48)
```go
func processZipUIDGidField(data []byte, file *zip.FileHeader) error {
	var ugField ZipUIDGidField
	err := binary.Read(bytes.NewReader(data), binary.LittleEndian, &ugField)
	if err != nil {
		return err
	}

	if !(ugField.Version == 1 && ugField.UIDSize == 4 && ugField.GIDSize == 4) {
		return errors.New("uid/gid data not supported")
	}

	return os.Lchown(file.Name, int(ugField.UID), int(ugField.Gid))
```

**File:** helpers/archives/zip_extra.go (L96-122)
```go
func processZipExtra(file *zip.FileHeader) error {
	if len(file.Extra) == 0 {
		return nil
	}

	r := bytes.NewReader(file.Extra)
	for {
		field, data, err := readZipExtraField(r)
		if err == io.EOF {
			break
		} else if err != nil {
			return err
		}

		switch field.Type {
		case ZipUIDGidFieldType:
			err = processZipUIDGidField(data, file)
		case ZipTimestampFieldType:
			err = processZipTimestampField(data, file)
		}
		if err != nil {
			return err
		}
	}

	return nil
}
```

**File:** commands/helpers/cache_extractor.go (L618-664)
```go
func (c *CacheExtractorCommand) Execute(cliContext *cli.Context) {
	log.SetRunnerFormatter()

	c.normalizeExtractorArgs()
	if err := validateCacheTransferTuning(c.TransferBufferSize, c.ChunkSize, c.Concurrency); err != nil {
		logrus.Fatalln(err)
	}

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
}
```
