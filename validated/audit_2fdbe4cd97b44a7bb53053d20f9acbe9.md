### Title
Raw (and gzip) archivers do not enforce the "children of dir" containment invariant, allowing symlink-based reads outside the archive root - ([File: commands/helpers/archive/raw/raw_archiver.go])

### Summary
`archive.NewArchiver`'s documented invariant states "the archiver will ensure that files to be archived are children of the directory provided" [1](#0-0) , but the raw archiver's `Archive` method never checks the `dir` field it stores, and simply `os.Open`s whatever `pathname` key appears in the `files` map before copying its contents to the writer [2](#0-1) . Since `os.Open` follows symlinks, a symlink entry inside the build directory whose target resolves outside of it will have its target's content copied into the artifact/cache stream, unlike the `tarzstd` archiver which explicitly enforces this boundary [3](#0-2) .

### Finding Description
The `files` map is populated by `fileArchiver.add`, which uses `os.Lstat` (not following symlinks) to store the symlink's own `FileInfo` under its in-repo path key [4](#0-3) . The containment check performed in `fileArchiver.process`/`findRelativePathInProject` only validates that the *symlink's own path* (not its resolved target) lies under the working directory [5](#0-4) , [6](#0-5) . A pipeline author fully controls repository content (including symlinks committed to the repo, or symlinks created by job scripts) and controls `artifacts:paths`/`artifacts:format` via `.gitlab-ci.yml`, as demonstrated by the `dotenv`/raw artifact wiring in `spec.Artifact` and `AbstractShell.writeUploadArtifact` [7](#0-6) . When `archive.Raw` is selected, `archiver.Archive` (raw) receives the symlink pathname in the map and directly `os.Open`s it, following the symlink to any target the archiving process can read, with no re-validation against `a.dir` (which is stored but unused) [8](#0-7) . This contrasts with `tarzstd`'s archiver, which explicitly rejects paths whose resolved absolute path escapes `a.dir` and additionally never reads file *content* through a symlink (`if !fi.Mode().IsRegular() { continue }`) [9](#0-8) . The `gziplegacy` archiver has the same missing-check pattern (it just sorts filenames and hands them to `archives.CreateGzipArchive` without any containment check) [10](#0-9) .

### Impact Explanation
This lets a job author cause the archiving process (Runner helper binary, potentially running as a separate helper container/process in Docker/Kubernetes executors) to read and exfiltrate the contents of any file that process can access on its own filesystem view via a symlink checked into the repository, packaged and uploaded as a job artifact or pushed to cache storage — bypassing the documented "children of the directory provided" containment guarantee that other archivers (`tarzstd`) do enforce.

### Likelihood Explanation
Fully reachable by an unprivileged pipeline author: commit/create a symlink in the checkout directory pointing outside it, reference that symlink path in `artifacts:paths` with `format: raw` (or `gzip`), and let the Runner-side `ArtifactsUploaderCommand`/cache archiver invoke the raw/gzip archiver. No special runner or admin configuration is required beyond a job being able to run `ln -s /some/target ./link` and set artifact paths, both of which are ordinary, expected pipeline-author capabilities.

### Recommendation
Add an explicit containment check in `raw` (and `gziplegacy`) `Archive`, mirroring `tarzstd_archiver.go`: resolve `filepath.Abs`/`filepath.EvalSymlinks` on the target path and reject (with an error) any path whose resolved location is not `a.dir` or a descendant of it, rather than relying solely on the pre-check done in `fileArchiver`, which only validates the symlink's own path and not its target.

### Proof of Concept
```go
func TestRawArchiver_RejectsSymlinkEscapingDir(t *testing.T) {
    dir := t.TempDir()
    outside := t.TempDir()
    secret := filepath.Join(outside, "secret.txt")
    require.NoError(t, os.WriteFile(secret, []byte("SECRET-DATA"), 0o644))

    link := filepath.Join(dir, "escape-link")
    require.NoError(t, os.Symlink(secret, link))

    fi, err := os.Lstat(link)
    require.NoError(t, err)

    buf := new(bytes.Buffer)
    a, err := archive.NewArchiver(archive.Raw, buf, dir, archive.DefaultCompression)
    require.NoError(t, err)

    err = a.Archive(context.Background(), map[string]os.FileInfo{link: fi})

    // Expected (fixed) behavior: containment error, no data copied
    require.Error(t, err)
    assert.NotContains(t, buf.String(), "SECRET-DATA")
    // Current (vulnerable) behavior: err == nil and buf.String() == "SECRET-DATA"
}
```

### Citations

**File:** commands/helpers/archive/archive.go (L86-89)
```go
// NewArchiver returns a new Archiver of the specified format.
//
// The archiver will ensure that files to be archived are children of the
// directory provided.
```

**File:** commands/helpers/archive/raw/raw_archiver.go (L22-49)
```go
type archiver struct {
	w   io.Writer
	dir string
}

// NewArchiver returns a new Raw Archiver.
func NewArchiver(w io.Writer, dir string, level archive.CompressionLevel) (archive.Archiver, error) {
	return &archiver{w: w, dir: dir}, nil
}

// Archive opens and copies a single file to the writer passed to
// NewRawArchiver. If more than one file is passed, ErrTooManyRawFiles is
// returned.
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

**File:** commands/helpers/archive/tarzstd/tarzstd_archiver.go (L62-106)
```go
	for _, name := range sorted {
		fi := files[name]
		if fi.Mode()&irregularModes != 0 {
			continue
		}

		path, err := filepath.Abs(name)
		if err != nil {
			return err
		}
		if !strings.HasPrefix(path, a.dir+string(filepath.Separator)) && path != a.dir {
			return fmt.Errorf("%s cannot be archived from outside of chroot (%s)", name, a.dir)
		}

		rel, err := filepath.Rel(a.dir, path)
		if err != nil {
			return err
		}

		if ctx.Err() != nil {
			return ctx.Err()
		}

		var link string
		if fi.Mode()&os.ModeSymlink != 0 {
			link, err = os.Readlink(path)
			if err != nil {
				return err
			}
		}

		hdr, err := tar.FileInfoHeader(fi, link)
		if err != nil {
			return err
		}
		hdr.Name = rel
		if fi.IsDir() {
			hdr.Name += "/"
		}

		if err := tw.WriteHeader(hdr); err != nil {
			return err
		}

		if !fi.Mode().IsRegular() {
```

**File:** commands/helpers/file_archiver.go (L65-88)
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

**File:** commands/helpers/file_archiver.go (L203-216)
```go
	abs, err := filepath.Abs(base)
	if err != nil {
		return "", fmt.Errorf("could not resolve artifact absolute path %s: %w", path, err)
	}

	rel, err := filepath.Rel(c.wd, abs)
	if err != nil {
		return "", fmt.Errorf("could not resolve artifact relative path %s: %w", path, err)
	}

	// If fully resolved relative path begins with ".." it is not a subpath of our working directory
	if strings.HasPrefix(rel, ".."+string(filepath.Separator)) || rel == ".." {
		return "", fmt.Errorf("artifact path is not a subpath of project directory: %s", path)
	}
```

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

**File:** commands/helpers/archive/gziplegacy/gzip_legacy_archiver.go (L29-37)
```go
func (a *archiver) Archive(ctx context.Context, files map[string]os.FileInfo) error {
	sorted := make([]string, 0, len(files))
	for filename := range files {
		sorted = append(sorted, filename)
	}
	sort.Strings(sorted)

	return archives.CreateGzipArchive(a.w, sorted)
}
```
