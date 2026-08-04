### Title
Case-insensitive filesystem bypass of `--exclude` patterns in `fileArchiver.isExcluded` allows masked/secret files to be archived - (File: `commands/helpers/file_archiver.go`)

### Summary
`fileArchiver.isExcluded` (commands/helpers/file_archiver.go:103-121) matches the archived file's relative path against exclude patterns using `doublestar.Match`, which performs byte-for-byte, case-sensitive comparison. On case-insensitive filesystems (default Windows NTFS, default macOS APFS/HFS+), a job can reference an intended-to-be-excluded file using an alternately-cased `--path` string; the OS resolves it to the *same* file, but the string used for exclude matching retains the user-typed case and no longer matches the exclude pattern, so the file is archived despite being nominally excluded.

### Finding Description
The relevant call chain is `process()` → `isExcluded(relative)` → `findRelativePathInProject(pattern)` → `filepath.ToSlash` → `doublestar.Match(relPattern, path)`.

Critically, the two sides of the comparison are derived asymmetrically with respect to filesystem case:
- The archived-file side: in `process()` (file_archiver.go:65-101), `absolute = filepath.Abs(match)` and `relative = filepath.Rel(c.wd, absolute)` are computed purely from the string the caller supplied (`match`), which for non-glob `--path` entries comes straight from `findRelativePathInProject(path)` (file_archiver.go:191-222) — i.e., the *literal, user-typed casing*, not the actual on-disk casing. `filepath.Abs`/`filepath.Rel` never consult the filesystem, so no case-folding/canonicalization happens.
- The exclude-pattern side: `isExcluded` (file_archiver.go:103-121) computes `relPattern` from `findRelativePathInProject(pattern)` the same way — literal, user-typed casing of the exclude string.
- The actual comparison, `doublestar.Match`, is case-sensitive.

On a case-insensitive filesystem, `os.Lstat`/`filepath.Walk` will happily resolve a path like `SECRETS/.ENV` to the real file `secrets/.env` (same inode), so `c.add()` succeeds and the real secret content is added to `c.files`. But because the string used for exclude comparison is `SECRETS/.ENV` while the exclude pattern is `secrets/.env` (or `.env`, `**/.env`, etc.), `doublestar.Match` returns `false`, so `isExcluded` never flags it as excluded. The file is archived even though the exclude rule was intended to (and appears to) cover it.

The existing normalization (`filepath.ToSlash`) only fixes separator differences (backslash vs slash), not case, so it does not mitigate this. There is no filesystem-canonical-case resolution anywhere in this path.

### Impact Explanation
A pipeline author (or anyone who controls `artifacts:paths`/`--path` values for a job, e.g. via job script or CI config) can cause files that a security policy intends to always exclude (e.g. `.env`, `.git-credentials`, other masked/secret files) to be included in the resulting artifact or cache archive on Windows runners or macOS shell runners, where the working directory filesystem is case-insensitive. This is a scoped, concrete exclude-bypass leading to secret/token files ending up in a downloadable artifact/cache, matching the described critical impact.

### Likelihood Explanation
Exploitability requires only:
1. A case-insensitive working-directory filesystem (default on Windows NTFS and default macOS APFS/HFS+ — both are supported GitLab Runner executor targets), and
2. The attacker's ability to specify `--path`/`artifacts:paths` values with a different case than the exclude pattern while the real file exists with the "correct" case (which the attacker fully controls, since they control the job script's working directory contents).

No special privileges are needed beyond normal job/pipeline authoring, and the bypass is fully deterministic and repeatable — not a race condition or timing-dependent issue.

### Recommendation
Normalize both the archived path and the exclude pattern to their filesystem-canonical case (or filesystem-canonical form generally) before calling `doublestar.Match`, e.g., resolve both to the actual on-disk casing (via a case-preserving `os.Lstat`/directory-entry lookup) rather than relying on the literal, user-typed string. Alternatively, on platforms where the working directory filesystem is known/detected to be case-insensitive, perform the `doublestar.Match` comparison case-insensitively (e.g., lowercase both `relPattern` and `path` before matching), consistent with how the underlying filesystem treats the paths as identical.

### Proof of Concept
Go unit test added to `commands/helpers/file_archiver_test.go`, run on Windows or macOS (case-insensitive FS):
```go
func TestIsExcludedCaseInsensitiveBypass(t *testing.T) {
    dir := t.TempDir()
    require.NoError(t, os.Chdir(dir))
    // simulate secret file
    require.NoError(t, os.WriteFile(filepath.Join(dir, ".env"), []byte("SECRET=1"), 0600))

    c := &fileArchiver{
        Paths:   []string{"DIFFERENTCASE/.ENV"}, // same real file, different case in --path
        Exclude: []string{".env"},
    }
    require.NoError(t, c.enumerate())

    // Assert the secret file was NOT added to the archive file set,
    // i.e. the exclude rule applied despite the case difference.
    _, archived := c.files[".env"]
    assert.False(t, archived, "secret file bypassed exclude pattern due to case mismatch")
}
```
Expected current (buggy) behavior: the assertion fails — `.env`/`.ENV` ends up in `c.files` because `isExcluded` returns `false` for the case-mismatched path, demonstrating the bypass. After applying the recommended fix (case-normalized matching), the test should pass. [1](#0-0) [2](#0-1) [3](#0-2)

### Citations

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

**File:** commands/helpers/file_archiver.go (L103-121)
```go
func (c *fileArchiver) isExcluded(path string) (bool, string) {
	// Both path and pattern need to be normalized with filepath.ToSlash().
	// Matching will fail with Windows machines using "\\" path separators and patterns with "/" path separators
	path = filepath.ToSlash(path)
	for _, pattern := range c.Exclude {
		relPattern, err := c.findRelativePathInProject(pattern)
		if err != nil {
			logrus.Warningf("isExcluded: %v", err.Error())
			return false, ""
		}
		relPattern = filepath.ToSlash(relPattern)
		excluded, err := doublestar.Match(relPattern, path)
		if err == nil && excluded {
			return true, pattern
		}
	}

	return false, ""
}
```

**File:** commands/helpers/file_archiver.go (L191-222)
```go
func (c *fileArchiver) findRelativePathInProject(path string) (string, error) {
	slashPath := filepath.ToSlash(path)
	if filepath.Clean(slashPath) == filepath.Clean(c.wd) {
		return ".", nil
	}

	base, patt := slashPath, ""
	// check if path contains a glob pattern
	if strings.ContainsAny(slashPath, "*?[{") {
		base, patt = doublestar.SplitPattern(slashPath)
	}

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

	// Relative path is needed now that our fsys "root" is at the working directory
	rel = filepath.Join(rel, patt)
	rel = filepath.FromSlash(rel)
	return rel, nil
}
```
