### Title
Unbounded decompression in tar+zstd extractor allows disk-exhaustion DoS - ([File: commands/helpers/archive/tarzstd/tarzstd_extractor.go])

### Summary
`extractor.Extract` copies each tar entry's content with `io.Copy(f, tr)` and never enforces any cap on the number of bytes written, relying solely on the tar header's `Size` field and the underlying `zstd.Reader` to bound output. A tar entry with an attacker-set large `Size` combined with a highly-compressible zstd stream will cause the runner to write an effectively unbounded amount of data to the host disk during cache/artifact extraction.

### Finding Description
`Extract` at [1](#0-0)  creates the destination file and streams tar content into it with `io.Copy(f, tr)`, with no byte-count limit, no free-disk check, and no cap on cumulative extracted bytes across the whole archive. The reader chain is `zstd.NewReader(...)` → `tar.NewReader(zr)` [2](#0-1) ; the tar entry's declared `Size` is attacker-controlled inside the tar stream, and `archive/tar`'s `Reader.Read` only stops once that many bytes have been produced for the current entry (or the underlying reader errors). Since zstd supports very high compression ratios, a small compressed artifact/cache blob can decode to a very large amount of data per entry, and the code allows this to happen for every regular file entry with no guard.

This code path is reachable via the `cache-extractor` helper command, invoked by `CacheExtractorCommand.Execute` after `openArchive`/`archive.NewExtractor` [3](#0-2) , which any pipeline author can trigger by pushing a cache archive (or artifact) that this runner will later download and extract. There is no size cap enforced before or during `Extract` for either cache or artifact extraction in this codebase — the `MaxUploadedArchiveSize` config found elsewhere only bounds cache *upload* size, not the decompressed size on *extraction*, and no `LimitReader`/decompressed-size check exists in the tar+zstd extractor.

### Impact Explanation
If exploited, a single job's cache/artifact extraction can consume arbitrary disk space on the shared runner host (bounded only by available disk), which can starve other concurrently-running jobs on the same host of disk space for their own container filesystems, logs, or Docker layer storage. This is a legitimate, low-effort denial-of-service against the shared host's I/O/disk resources triggerable purely with attacker-supplied CI artifact/cache content.

However, the specific mechanism alleged in the question — that this specifically and reliably breaks the `terminalConn.Start()`/session proxy path in `executors/docker/terminal.go` (i.e., `t.executor.waiter.Wait`) — is not substantiated by the code. `terminalConn.Start` and `waiter.Wait` depend on the Docker daemon's ability to create/attach an exec session and on `ContainerWait`, which are not shown in this codebase to have any direct dependency on the host's free disk space beyond generic Docker daemon degradation under disk pressure (an indirect, non-deterministic effect, not a designed call path). No code in `wait.go` or `terminal.go` reads/writes to disk in a way tied to the extractor. The cross-job terminal-session failure is plausible only as a generic consequence of host resource exhaustion (any disk-hungry process on a shared host can degrade Docker daemon responsiveness), not as a specific, reproducible interaction between `Extract` and the terminal/session subsystem.

### Likelihood Explanation
Feasible: no privileged access is required — a normal pipeline author who can set a cache/artifact to be created and later restored can plant a crafted tar+zstd stream. Preconditions: the runner must fetch and extract the crafted cache/artifact (standard job flow) with no configured size limit stopping it. This is straightforward to reproduce with a local fuzz/PoC using highly repetitive input data and `zstd`'s high compression ratio.

### Recommendation
Enforce a decompressed-size cap during extraction: wrap `tr` (or the file writer) with an `io.LimitReader`/counting writer bounded by a configurable maximum total-extracted-size (and optionally per-file size), aborting extraction with an error when exceeded, independent of `hdr.Size`. Apply consistently across all extractor implementations (`tarzstd`, `fastzip`, `ziplegacy`, `gziplegacy`) for parity. Consider also checking available disk space before/during extraction and canceling on threshold breach tied to `ctx`.

### Proof of Concept
Go test idea (add to `commands/helpers/archive/tarzstd` package):
```go
func TestExtract_DecompressionBombUnbounded(t *testing.T) {
    dir := t.TempDir()
    var buf bytes.Buffer
    zw, _ := zstd.NewWriter(&buf, zstd.WithEncoderLevel(zstd.SpeedBestCompression))
    tw := tar.NewWriter(zw)
    hdr := &tar.Header{Name: "bomb.txt", Mode: 0600, Size: 10 << 30} // 10 GiB claimed size
    _ = tw.WriteHeader(hdr)
    zeros := make([]byte, 1<<20)
    for written := int64(0); written < hdr.Size; written += int64(len(zeros)) {
        tw.Write(zeros) // highly compressible, writes to tar/zstd
    }
    tw.Close()
    zw.Close()

    r := bytes.NewReader(buf.Bytes())
    ext, _ := NewExtractor(r, int64(r.Len()), dir)

    done := make(chan error, 1)
    go func() { done <- ext.Extract(context.Background()) }()

    // Assert: extraction should fail fast with a "size limit exceeded" error
    // instead of writing gigabytes to disk. Currently it does not.
    select {
    case err := <-done:
        assert.Error(t, err) // expected after fix; currently succeeds / runs unbounded
    case <-time.After(5 * time.Second):
        t.Fatal("extraction did not complete/limit as expected")
    }
}
```
Assertions: (1) after a fix, `Extract` returns an error once a configured max-decompressed-size threshold is crossed, well before writing the full claimed 10 GiB; (2) disk usage in `dir` never exceeds the configured cap. Note: the claim that this specifically starves `terminalConn.Start()`/`waiter.Wait` for a *different, concurrently running job* was not verified against concrete code coupling and should be treated as an indirect/unproven secondary effect rather than a demonstrated reachable path.

### Citations

**File:** commands/helpers/archive/tarzstd/tarzstd_extractor.go (L33-40)
```go
func (e *extractor) Extract(ctx context.Context) error {
	zr, err := zstd.NewReader(io.NewSectionReader(e.r, 0, e.size), zstd.WithDecoderLowmem(true))
	if err != nil {
		return err
	}
	defer zr.Close()

	tr := tar.NewReader(zr)
```

**File:** commands/helpers/archive/tarzstd/tarzstd_extractor.go (L87-99)
```go
		case fi.Mode().IsRegular():
			f, err := os.Create(path)
			if err != nil {
				return err
			}

			if _, err := io.Copy(f, tr); err != nil {
				f.Close()
				return err
			}
			if err := f.Close(); err != nil {
				return err
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
