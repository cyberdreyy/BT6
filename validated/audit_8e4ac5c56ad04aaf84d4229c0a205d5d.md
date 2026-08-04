## Confirmed: `.gitlab-ci.yml` `artifacts:format: raw` combined with `artifacts:paths` is fully attacker-controlled (job author writes the YAML), and the resulting file set (built with `os.Lstat` in `fileArchiver.add`) is handed unfiltered to `archiver.Archive`. [1](#0-0) [2](#0-1) 

### Summary
`raw_archiver.go`'s `Archive` calls `os.Open` and `io.Copy` on any path present in the `files` map without checking `fi.Mode()&os.ModeType`, unlike the zip archiver which explicitly skips `os.ModeNamedPipe`, `os.ModeSocket`, and `os.ModeDevice` entries. A job author can commit/create a FIFO (`mkfifo`) inside the build workspace matching an `artifacts:paths` entry with `artifacts:format: raw`, causing the runner's `artifacts-uploader` helper process to block indefinitely on `io.Copy`, or to stream attacker-influenced/unbounded data through the upload pipe if the FIFO is fed by another process.

### Finding Description
`fileArchiver.add` populates the `files` map using `os.Lstat`, and `processPath`'s `filepath.Walk` will visit any filesystem entry type (regular file, FIFO, device node, socket) matching the glob under `artifacts:paths`, since nothing in `process`/`add` filters on file type. [3](#0-2)  That map is passed straight into `archive.Archiver.Archive`. For `spec.ArtifactFormatRaw` (only usable when a single path is configured), `raw.archiver.Archive` calls `os.Open(pathname)` followed by `io.Copy(a.w, f)` with no `os.ModeType` check: [4](#0-3) . Compare this to `helpers/archives/zip_create.go`'s `createZipEntry`, which explicitly special-cases `os.ModeNamedPipe, os.ModeSocket, os.ModeDevice` and skips them with a warning rather than opening/copying: [5](#0-4) . The raw archiver has no equivalent guard.

The call chain from the job's perspective: `ArtifactsUploaderCommand.createBodyProvider` spins up a goroutine that calls `archiver.Archive(ctx, c.files)` and then `pw.CloseWithError(archiveErr)` on the write end of an `io.Pipe`; the read end is streamed to the network client as the multipart upload body: [6](#0-5) . If `Archive` blocks forever on `io.Copy` (because the FIFO has no writer, or a writer that never closes), `pw` is never closed, `pr` (the meter-wrapped reader used as the HTTP request body) never signals EOF, and the upload/goroutine hangs until the command's own `--timeout` for the network operation elapses (or indefinitely for the local `io.Copy`/goroutine, since that goroutine has no cancellation tied to `ctx`, whose value is unused inside `Archive`).

### Impact Explanation
This is resource exhaustion / hang scoped to the runner host process running the `artifacts-uploader` helper (and its goroutine), not sandbox escape. If the FIFO has an unbounded/never-closing writer process on the same host/container (which a job could itself spawn to feed the pipe), `io.Copy` will stream arbitrary attacker-supplied bytes through the artifacts upload channel, effectively exfiltrating attacker-chosen data as if it were job-produced artifact content — though this data still originates from the job's own execution environment, so exfiltration impact is limited to bypassing size/content expectations of "file" artifacts, not accessing data outside the job's sandbox. The primary concrete impact is a goroutine/file-descriptor leak and an artifact upload attempt that never completes (relying on the overall job/network timeout to eventually abort), degrading runner host resources if repeated across many jobs.

### Likelihood Explanation
Precondition: attacker needs `.gitlab-ci.yml` authorship (a normal CI user) and the ability to run shell commands to `mkfifo` in the workspace, plus `artifacts: {paths: [fifo], format: raw}` — both are ordinary, always-available CI features requiring no special runner configuration. This is trivially reproducible in shell/docker executors.

### Recommendation
In `commands/helpers/archive/raw/raw_archiver.go`, before `os.Open`, check `files[pathname].Mode()&os.ModeType` and reject/skip non-regular files (`ModeNamedPipe`, `ModeSocket`, `ModeDevice`, `ModeCharDevice`), mirroring the guard already present in `helpers/archives/zip_create.go`'s `createZipEntry`. Additionally, consider passing `ctx` down into `io.Copy` (e.g., via a context-aware copy or `os.File.SetReadDeadline`) so a hang can be bounded even for legitimately-typed files that never yield EOF.

### Proof of Concept
```go
func TestRawArchiver_RejectsFIFO(t *testing.T) {
    dir := t.TempDir()
    fifoPath := filepath.Join(dir, "fifo")
    require.NoError(t, syscall.Mkfifo(fifoPath, 0600)) // Unix-only

    info, err := os.Lstat(fifoPath)
    require.NoError(t, err)

    buf := new(bytes.Buffer)
    a, err := raw.NewArchiver(buf, dir, archive.DefaultCompression)
    require.NoError(t, err)

    files := map[string]os.FileInfo{fifoPath: info}

    done := make(chan error, 1)
    go func() { done <- a.Archive(context.Background(), files) }()

    select {
    case err := <-done:
        require.Error(t, err) // expect Archive to reject non-regular files
    case <-time.After(2 * time.Second):
        t.Fatal("Archive blocked on FIFO instead of returning an error")
    }
}
```
Expected current behavior (bug): the test times out because `Archive` blocks on `io.Copy` reading from the FIFO with no writer. Expected fixed behavior: `Archive` returns promptly with an error (or skips the entry), matching the zip archiver's handling of special file types.

### Citations

**File:** common/spec/spec.go (L378-398)
```go
type ArtifactFormat string

const (
	ArtifactFormatDefault ArtifactFormat = ""
	ArtifactFormatZip     ArtifactFormat = "zip"
	ArtifactFormatGzip    ArtifactFormat = "gzip"
	ArtifactFormatRaw     ArtifactFormat = "raw"
	ArtifactFormatZipZstd ArtifactFormat = "zipzstd"
	ArtifactFormatTarZstd ArtifactFormat = "tarzstd"
)

type Artifact struct {
	Name      string          `json:"name" inputs:"expand"`
	Untracked bool            `json:"untracked"`
	Paths     ArtifactPaths   `json:"paths" inputs:"expand"`
	Exclude   ArtifactExclude `json:"exclude" inputs:"expand"`
	When      ArtifactWhen    `json:"when" inputs:"expand"`
	Type      string          `json:"artifact_type"`
	Format    ArtifactFormat  `json:"artifact_format"`
	ExpireIn  string          `json:"expire_in" inputs:"expand"`
}
```

**File:** commands/helpers/file_archiver.go (L65-101)
```go
func (c *fileArchiver) process(match string) bool {
	var absolute, relative string
	var err error

	absolute, err = filepath.Abs(match)
	if err == nil {
		// Let's try to find a real relative path to an absolute from working directory
		relative, err = filepath.Rel(c.wd, absolute)
	}

	if err == nil {
		// Process path only if it lives in our build directory
		if !strings.HasPrefix(relative, ".."+string(filepath.Separator)) {
			excluded, rule := c.isExcluded(relative)
			if excluded {
				c.exclude(rule)
				return false
			}

			err = c.add(relative)
		} else {
			err = errors.New("not supported: outside build directory")
		}
	}

	if err == nil {
		return true
	}

	if os.IsNotExist(err) {
		// We hide the error that file doesn't exist
		return false
	}

	logrus.Warningf("%s: %v", match, err)
	return false
}
```

**File:** commands/helpers/file_archiver.go (L127-138)
```go
func (c *fileArchiver) add(path string) error {
	// Always use slashes
	path = filepath.ToSlash(path)

	// Check if file exist
	info, err := os.Lstat(path)
	if err == nil {
		c.files[path] = info
	}

	return err
}
```

**File:** commands/helpers/archive/raw/raw_archiver.go (L35-49)
```go
func (a *archiver) Archive(ctx context.Context, files map[string]os.FileInfo) error {
	if len(files) > 1 {
		return ErrTooManyRawFiles
	}

	for pathname := range files {
		f, err := os.Open(pathname)
		if err != nil {
			return err
		}
		defer f.Close()

		_, err = io.Copy(a.w, f)
		return err
	}
```

**File:** helpers/archives/zip_create.go (L68-82)
```go
	switch fi.Mode() & os.ModeType {
	case os.ModeDir:
		return createZipDirectoryEntry(archive, fh)

	case os.ModeSymlink:
		return createZipSymlinkEntry(archive, fh)

	case os.ModeNamedPipe, os.ModeSocket, os.ModeDevice:
		// Ignore files of these types
		logrus.Warningln("File ignored:", fileName)
		return nil

	default:
		return createZipFileEntry(archive, fh)
	}
```

**File:** commands/helpers/artifacts_uploader.go (L111-136)
```go
	// Create a StreamProvider that doesn't know its content length in advance
	streamProvider := common.StreamProvider{
		ReaderFactory: func() (io.ReadCloser, error) {
			pr, pw := io.Pipe()

			archiver, archiveErr := archive.NewArchiver(archive.Format(format), pw, c.wd, GetCompressionLevel(c.CompressionLevel))
			if archiveErr != nil {
				pr.CloseWithError(archiveErr)
				return nil, archiveErr
			}

			// Start a new Goroutine to create the archive for this attempt
			go func() {
				archiveErr := archiver.Archive(context.Background(), c.files)
				pw.CloseWithError(archiveErr)
			}()

			meteredReader := meter.NewReader(
				pr,
				c.TransferMeterFrequency,
				meter.LabelledRateFormat(os.Stdout, "Uploading artifacts", meter.UnknownTotalSize),
			)

			return meteredReader, nil
		},
	}
```
