### Title
Raw archiver follows attacker-planted symlinks, allowing artifact upload to exfiltrate arbitrary host files - ([File: commands/helpers/archive/raw/raw_archiver.go])

### Summary
The `raw` format archiver's `Archive` method opens the single file referenced by the `files` map key with `os.Open(pathname)`, which follows symlinks. Because the artifact enumeration logic (`fileArchiver.process`/`fileArchiver.add`) only lexically validates that the *symlink's own path* is inside the job's working directory (using `filepath.Abs`/`filepath.Rel`, never `filepath.EvalSymlinks`), a job can place a symlink inside the build directory pointing at an arbitrary file outside it, reference that symlink via `artifacts:paths`, and have Runner read and upload the target file's contents as if it were a normal build artifact.

### Finding Description
`fileArchiver.add` in `commands/helpers/file_archiver.go` stores files using `os.Lstat` (line 132), which is symlink-aware for classification purposes, but the path-containment check in `fileArchiver.process` (lines 65-101) only validates that the *symlink itself* resolves to a path under `c.wd`; it never calls `filepath.EvalSymlinks` to check where the symlink target actually points. As a result, a symlink created anywhere under the job workspace (e.g. `ln -s /etc/shadow leak` or a symlink to a mounted secret file) is accepted into `c.files` as long as the symlink file name matches the glob pattern given in `artifacts:paths`.

That `files` map (keyed by the relative pathname) is passed unchanged to `archiver.Archive(ctx, c.files)` in `commands/helpers/artifacts_uploader.go` (line 124). When the artifact format is `raw` (`spec.ArtifactFormatDefault`/raw single-file uploads), `raw.archiver.Archive` in `commands/helpers/archive/raw/raw_archiver.go` (lines 35-51) does:
```go
for pathname := range files {
    f, err := os.Open(pathname)
    ...
    io.Copy(a.w, f)
}
```
`os.Open` follows symlinks by default (no `O_NOFOLLOW`), and the call happens with the process's actual working directory equal to the job's build directory, so opening the relative symlink path transparently reads whatever file the symlink points to — even outside the job workspace, including absolute host paths reachable by the job process's file permissions. The `dir` field passed to `NewArchiver` is stored on the raw archiver struct but is never used to validate or reject symlink targets; it's dead weight for this format.

None of the existing checks stop this: the containment check in `process()` is purely lexical/string based, `os.Lstat` classification does not prevent symlinks from being added to the archive set, and the raw archiver performs no `os.Lstat`/`filepath.EvalSymlinks` check on the resolved target before opening.

### Impact Explanation
An unprivileged CI job author can exfiltrate any file readable by the job process (e.g. mounted secrets, environment-derived credential files, or other host-accessible files depending on executor) by placing a symlink in the workspace pointing to that file and declaring it as an artifact path with raw format. The stolen content is delivered back to the attacker through GitLab's normal artifact download channel, which they already have access to as the job owner.

### Likelihood Explanation
This is trivially reproducible by any job author: `ln -s <target> leak_file` in a `before_script`/`script` step, followed by `artifacts: {paths: ["leak_file"]}` with raw artifact format (or any CI mechanism selecting the raw single-file format). No special runner configuration or privilege escalation is required, only the ability to create a symlink in the workspace, which any job script can do on shell/docker/kubernetes executors that write to a shared/host-visible filesystem.

### Recommendation
In `fileArchiver.add`/`process`, resolve and validate the target of symlinks (`filepath.EvalSymlinks`) and reject or exclude entries whose resolved real path escapes `c.wd`, or explicitly refuse to add symlink entries into `c.files` for raw archives. Additionally, harden `raw.archiver.Archive` to `os.Lstat` the pathname first and refuse (or explicitly resolve-and-revalidate) symlinks before calling `os.Open`, ensuring the resolved real path is a descendant of `a.dir`.

### Proof of Concept
```go
func TestRawArchiver_RefusesSymlinkEscape(t *testing.T) {
    dir := t.TempDir()
    outside := t.TempDir()
    secret := filepath.Join(outside, "secret.txt")
    require.NoError(t, os.WriteFile(secret, []byte("TOP SECRET"), 0600))

    link := filepath.Join(dir, "leak")
    require.NoError(t, os.Symlink(secret, link))

    info, err := os.Lstat(link)
    require.NoError(t, err)

    var buf bytes.Buffer
    a, err := raw.NewArchiver(&buf, dir, archive.DefaultCompression)
    require.NoError(t, err)

    err = a.Archive(context.Background(), map[string]os.FileInfo{link: info})

    // Expected (fixed) behavior: Archive must reject symlinks escaping dir.
    require.Error(t, err)
    require.NotContains(t, buf.String(), "TOP SECRET")
}
```
Currently this test fails: `Archive` succeeds and `buf` contains `"TOP SECRET"`, confirming the vulnerability. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

**File:** commands/helpers/archive/raw/raw_archiver.go (L35-51)
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

	return nil
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

**File:** commands/helpers/artifacts_uploader.go (L112-126)
```go
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
```
