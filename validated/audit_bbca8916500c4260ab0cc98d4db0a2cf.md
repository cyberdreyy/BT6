### Title
Cache key path traversal escapes CacheDir via unsanitized `AlternateArchiveFile` when `FF_HASH_CACHE_KEYS` is enabled - ([File: shells/abstract.go])

### Summary
The `--path` handling in `commands/helpers/file_archiver.go` (`fileArchiver.processPaths` → `findRelativePathInProject`) is properly guarded against directory traversal, so the specific hypothesis about job-controlled `--path` values escaping via `processPaths` does not hold. However, a related and real traversal exists in `shells/abstract.go`'s `newCacheConfig`/`cacheAlternateKey`, where the cache-key sanitizer is bypassed when `FF_HASH_CACHE_KEYS` is on, letting a job-controlled cache key produce an `AlternateArchiveFile` path that escapes `build.CacheDir`.

### Finding Description
`fileArchiver.processPath` (commands/helpers/file_archiver.go:146-189) resolves every `--path` entry through `findRelativePathInProject` [1](#0-0) , which rejects any path resolving outside the working directory (`strings.HasPrefix(rel, ".."...)`). This makes the archived-content selection (`--path`) safe against traversal — the premise in the question about `enumerate -> processPaths` is not exploitable.

The real issue is upstream, in how the *destination archive file path* is derived from the cache key in `newCacheConfig` (shells/abstract.go:133-208). Normally, `cachekey.Sanitize` [2](#0-1)  resolves `.`/`..` segments against a virtual root so traversal characters can never survive. But when `featureflags.HashCacheKeys` is on, the sanitizer is swapped for a no-op: [3](#0-2) 

The primary `ArchiveFile` is safe because it is built from `hashedKey` (a SHA-256 hex digest, fixed alphabet, no traversal chars). However, `AlternateArchiveFile` is built from `alternateKey`, and `cacheAlternateKey` returns the **raw, unsanitized `humanKey`** whenever hashing is enabled: [4](#0-3) 

`humanKey` comes directly from the job-controlled cache key (`cacheOptions.Key`, `cacheOptions.FallbackKeys`, or the `CACHE_FALLBACK_KEY` variable), only variable-expanded, not path-sanitized: [5](#0-4) [6](#0-5) 

That unsanitized key is joined into the file path via `getArchivePath`: [7](#0-6) 

Because `build.CacheDir` is `path.Join(cacheDir, ProjectUniqueDir)` [8](#0-7) , a key such as `../otherProjectUniqueDir/some-key` collapses out of the current project's cache subtree into a sibling project's cache directory on the shared runner cache disk. The `blockProtectedFallback` check on `CACHE_FALLBACK_KEY` only rejects a `-protected` suffix, not traversal sequences [9](#0-8) , so it does not stop this.

The resulting `AlternateArchiveFile` is passed to `commands/helpers/cache_archiver.go` as `--alternate-file`, where `tryRenameAlternateFile` operates on it directly with no path validation: [10](#0-9) 
`os.Stat(c.AlternateFile)` probes for existence of the crafted path, and if found, `os.Rename(c.AlternateFile, c.File)` moves that file into the current job's cache slot — an unvalidated cross-directory file move driven entirely by job-controlled input.

### Impact Explanation
On a shared runner (or any runner reused across projects sharing one cache-dir root), a job that sets `FF_HASH_CACHE_KEYS: "true"` and a crafted cache key/fallback key can construct a local `AlternateArchiveFile` path that resolves outside its own `CacheDir` into another project's cache subtree. Via `tryRenameAlternateFile`, this can move (steal, and delete the source of) another project's cached archive into the attacker's own build, or in the extraction path, be used to probe/consume unrelated cache archives it should have no access to — violating the "one job must not access another project's workload/cache" invariant.

### Likelihood Explanation
Requires: (1) `FF_HASH_CACHE_KEYS` enabled (a job/runner-level opt-in feature flag, not default-on) and (2) the attacker being able to guess or know the target project's `ProjectUniqueDir` segment (deterministic, project-ID derived, not secret) and a valid cache-key name within that project. Both are plausible for a runner shared across projects in the same GitLab instance/group. Exploitability is fully within an unprivileged job author's control (`.gitlab-ci.yml` `cache:key`/`cache:fallback_keys` or `CACHE_FALLBACK_KEY` variable), no admin action required.

### Recommendation
Apply `cachekey.Sanitize` (or an equivalent traversal-safe normalization) to `humanKey`/`alternateKey` unconditionally, regardless of `FF_HASH_CACHE_KEYS`, before it is used to build any filesystem path (`ArchiveFile` or `AlternateArchiveFile`). Additionally, validate in `cache_archiver.go`/`cache_extractor` that `File`/`AlternateFile` resolve within `build.CacheDir` before performing `os.Stat`/`os.Rename` operations, as defense in depth.

### Proof of Concept
Go unit test extending `TestNewCacheConfig` in shells/abstract_test.go:
```go
"hashed, traversal key escapes CacheDir": {
    cacheDir: "/cache",
    buildDir: "/builds/proj",
    userKey:  "../otherProjectDir/stolen-cache",
    ffs: map[string]bool{featureflags.HashCacheKeys: true},
},
```
Assert that `AlternateArchiveFile` (after `filepath.Rel(build.BuildDir, file)`) resolves to a path outside `/cache/<thisProjectUniqueDir>` — e.g. contains more `../` segments than needed to reach `/cache`, proving escape from the current project's cache root. A follow-up integration test in `commands/helpers/cache_archiver_test.go` can set `AlternateFile` to a file outside the test's tmp cache dir and assert `tryRenameAlternateFile` moves it despite being outside the expected sandbox.

### Citations

**File:** commands/helpers/file_archiver.go (L191-216)
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
```

**File:** cache/cachekey/cachekey.go (L27-56)
```go
func Sanitize(cacheKey string) (string, error) {
	if cacheKey == "" {
		return "", nil
	}

	// Decode percent-encoded chars and normalise separators, then
	// resolve traversals against a virtual root so ".." can never
	// escape beyond the root.
	cleaned := path.Clean("/" + normaliser.Replace(cacheKey))

	// Strip the leading "/" we added, split into segments, then walk
	// backwards trimming trailing whitespace from the rightmost
	// segments—dropping any that become empty.
	parts := strings.Split(cleaned[1:], "/")
	n := len(parts)
	for n > 0 {
		parts[n-1] = strings.TrimRightFunc(parts[n-1], unicode.IsSpace)
		if parts[n-1] != "" {
			break
		}
		n--
	}

	key := strings.Join(parts[:n], "/")

	if key == "" {
		return "", fmt.Errorf("cache key %q could not be sanitized", cacheKey)
	}

	return key, nil
```

**File:** shells/abstract.go (L120-126)
```go
func cacheAlternateKey(humanKey string, hashEnabled bool) string {
	sha256Key := fmt.Sprintf("%x", sha256.Sum256([]byte(humanKey)))
	if hashEnabled {
		return humanKey
	}
	return sha256Key
}
```

**File:** shells/abstract.go (L138-141)
```go
	rawKey := path.Join("/", build.JobInfo.Name, build.GitInfo.Ref)[1:]
	if userKey != "" {
		rawKey = build.GetAllVariables().ExpandValue(userKey)
	}
```

**File:** shells/abstract.go (L143-149)
```go
	hasher := func(s string) string { return s }
	sanitizer := cachekey.Sanitize
	// if hash key support is enabled, we don't need to sanitize keys anymore
	if build.IsFeatureFlagOn(featureflags.HashCacheKeys) {
		hasher = func(s string) string { return fmt.Sprintf("%x", sha256.Sum256([]byte(s))) }
		sanitizer = func(s string) (string, error) { return s, nil }
	}
```

**File:** shells/abstract.go (L173-183)
```go
	getArchivePath := func(key string) (string, error) {
		var err error
		file := path.Join(build.CacheDir, key, "cache.zip")
		if !build.IsFeatureFlagOn(featureflags.UsePowershellPathResolver) {
			file, err = filepath.Rel(build.BuildDir, file)
			if err != nil {
				return "", fmt.Errorf("inability to make the cache file path relative to the build directory (is the build directory absolute?)")
			}
		}
		return file, err
	}
```

**File:** shells/abstract.go (L314-329)
```go
	// the fallback cache keys from the cache config
	for _, cacheKey := range cacheOptions.FallbackKeys {
		addCacheConfig(buildVars.ExpandValue(cacheKey))
	}

	// the fallback key from CACHE_FALLBACK_KEY
	blockProtectedFallback := func(key string) bool {
		const blockedSuffix = "-protected"
		trimmedKey := strings.TrimRight(key, ". ")
		allowed := !strings.HasSuffix(trimmedKey, blockedSuffix)
		if !allowed {
			w.Warningf("CACHE_FALLBACK_KEY %q not allowed to end in %q", key, blockedSuffix)
		}
		return allowed
	}
	addCacheConfig(buildVars.Value("CACHE_FALLBACK_KEY"), blockProtectedFallback)
```

**File:** common/build.go (L509-512)
```go
	// to be able to use CI_BUILDS_DIR
	b.RootDir = rootDir
	b.CacheDir = path.Join(cacheDir, b.ProjectUniqueDir(false))
	b.RefreshAllVariables()
```

**File:** commands/helpers/cache_archiver.go (L253-284)
```go
func (c *CacheArchiverCommand) tryRenameAlternateFile() {
	if c.AlternateFile == "" || c.AlternateFile == c.File {
		return
	}

	_, err := os.Stat(c.File)
	if err == nil {
		logrus.Debugln("Primary cache file already exists locally, skipping rename from alternate")
		return
	}
	if !errors.Is(err, fs.ErrNotExist) {
		logrus.WithError(err).Warningln("Failed to stat primary cache file")
		return
	}

	if _, err := os.Stat(c.AlternateFile); err != nil {
		logrus.Debugln("Alternate cache file not found locally, nothing to rename")
		return
	}

	if err := os.MkdirAll(filepath.Dir(c.File), 0o700); err != nil {
		logrus.WithError(err).Warningln("Failed to create directory for cache file rename")
		return
	}

	if err := os.Rename(c.AlternateFile, c.File); err != nil {
		logrus.WithError(err).Warningln("Failed to rename alternate cache file to primary")
		return
	}

	logrus.Infoln("Renamed alternate cache file to primary")
}
```
