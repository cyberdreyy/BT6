### Title
Character-device files bypass the `os.ModeNamedPipe/os.ModeSocket/os.ModeDevice` skip branch in `createZipEntry`, causing `os.Open`+`io.Copy` on device nodes during archive creation - ([File: helpers/archives/zip_create.go])

### Summary
`createZipEntry` uses a `switch` on `fi.Mode() & os.ModeType` with exact-equality cases, but Go's `os` package sets **two** bits (`ModeDevice | ModeCharDevice`) for character-special files, so the masked value never equals the single-bit `os.ModeDevice` case and falls through to `default`, which calls `createZipFileEntry` and performs `os.Open`/`io.Copy` on the device node instead of skipping it. [1](#0-0) 

### Finding Description
`createZipEntry` classifies each path via `os.Lstat` and dispatches on `fi.Mode() & os.ModeType`, explicitly skipping `os.ModeNamedPipe, os.ModeSocket, os.ModeDevice` and otherwise treating the path as a regular file to be opened and copied into the zip stream. [2](#0-1) 

The bug is that `os.ModeType` is a bitmask combining several independent flags (`ModeDir | ModeSymlink | ModeNamedPipe | ModeSocket | ModeDevice | ModeCharDevice | ModeIrregular`), and for a character-special file Go's stat-to-`FileMode` conversion sets **both** `ModeDevice` and `ModeCharDevice` simultaneously. The `switch` case `os.ModeDevice` only matches when the masked value equals exactly that single bit (true for block devices), but for character devices the masked value is `ModeDevice|ModeCharDevice`, which matches none of the listed cases and therefore falls into `default: return createZipFileEntry(archive, fh)`, calling `os.Open(fh.Name)` on the device node. [3](#0-2) 

`CreateZipArchive` is reachable from attacker-controlled input: it is invoked by the legacy zip archiver used for cache/artifact archiving, which receives a map of file paths discovered under the job's build/cache directory and passes them straight through to `archives.CreateZipArchive`. [4](#0-3) 

An unprivileged pipeline author who controls the job script can create a character-special file inside the build directory with `mknod` (e.g., `mknod builddir/dev0 c 1 5` for `/dev/zero`-equivalent, or a custom device) provided the job container has `CAP_MKNOD`, which is part of Docker's default capability set for containers run as root (the common default for CI job images). Declaring that path as an `artifacts:paths` or `cache:paths` entry causes the runner helper to archive it, hitting the misclassified branch and opening the device file for reading.

### Impact Explanation
Opening and streaming from certain character devices can block indefinitely (denial of service on the archiving helper/runner process) or return effectively unbounded/unexpected data (e.g., `/dev/zero`), causing the archive step to hang or produce a bloated/garbage artifact. This is a concrete availability impact on the job's artifact/cache upload step, self-triggered from user-controlled build-directory contents, not merely theoretical.

### Likelihood Explanation
Requires the job container to have `CAP_MKNOD` (true by default for Docker executor jobs running as root, which is common) and the attacker to reference the created node path in `artifacts:paths`/`cache:paths`. This is fully reproducible and repeatable without any admin action — a normal pipeline author can craft the `.gitlab-ci.yml` and script to trigger it.

### Recommendation
Replace the exact-equality `switch` with explicit bit tests, e.g. check `fi.Mode()&os.ModeCharDevice != 0` or `fi.Mode()&(os.ModeDevice|os.ModeCharDevice|os.ModeNamedPipe|os.ModeSocket|os.ModeIrregular) != 0` and skip in that combined case, rather than relying on `fi.Mode()&os.ModeType` equaling a single flag constant. Also add an explicit `os.ModeIrregular` skip case for completeness.

### Proof of Concept
```go
func TestCreateZipEntry_SkipsCharDevice(t *testing.T) {
    dir := t.TempDir()
    devPath := filepath.Join(dir, "chardev")
    // requires CAP_MKNOD; run as root in CI container
    require.NoError(t, unix.Mknod(devPath, unix.S_IFCHR|0644, int(unix.Mkdev(1, 5)))) // /dev/zero-like

    var buf bytes.Buffer
    done := make(chan error, 1)
    go func() { done <- archives.CreateZipArchive(&buf, []string{devPath}) }()

    select {
    case err := <-done:
        require.NoError(t, err)
        // Assert the device entry was skipped, not embedded with real content
        zr, _ := zip.NewReader(bytes.NewReader(buf.Bytes()), int64(buf.Len()))
        for _, f := range zr.File {
            require.NotEqual(t, "chardev", filepath.Base(f.Name), "device file should have been skipped")
        }
    case <-time.After(5 * time.Second):
        t.Fatal("CreateZipArchive blocked on device file read - DoS confirmed")
    }
}
```
Expected current behavior: the test times out or embeds device content, demonstrating the misclassification; after the fix, the device is skipped and the function returns promptly.

### Citations

**File:** helpers/archives/zip_create.go (L52-82)
```go
func createZipEntry(archive *zip.Writer, fileName string) error {
	fi, err := os.Lstat(fileName)
	if err != nil {
		logrus.Warningln("File ignored:", err)
		return nil
	}

	fh, err := zip.FileInfoHeader(fi)
	if err != nil {
		return err
	}
	fh.Name = fileName
	fh.Extra = createZipExtra(fi)
	// Set EFS flag to indicate that filenames and comments are UTF-8 encoded
	fh.Flags |= 0x800

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

**File:** commands/helpers/archive/ziplegacy/zip_legacy_archiver.go (L34-43)
```go
// Archive archives all files as new gzip streams.
func (a *archiver) Archive(ctx context.Context, files map[string]os.FileInfo) error {
	sorted := make([]string, 0, len(files))
	for filename := range files {
		sorted = append(sorted, filename)
	}
	sort.Strings(sorted)

	return archives.CreateZipArchive(a.w, sorted)
}
```
