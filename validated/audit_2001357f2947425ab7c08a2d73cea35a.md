### Title
Unsanitized job cache key under `FF_HASH_CACHE_KEYS` produces an out-of-cache-root `AlternateFile` path used by `os.Rename` - (`shells/abstract.go`, `commands/helpers/cache_archiver.go`)

### Summary
When `FF_HASH_CACHE_KEYS` is enabled, `newCacheConfig` in `shells/abstract.go` computes the primary `ArchiveFile` from a SHA-256 hash of the cache key (safe), but computes `AlternateArchiveFile` from the **raw, unsanitized** human-readable key via `cacheAlternateKey`. Because a job can freely set `cache:key` (expanded through CI variables), and no path-traversal sanitation (`cachekey.Sanitize`) is applied to it in this mode, the resulting `AlternateArchiveFile` (passed to `cache-archiver`/`cache-extractor` as `--alternate-file`) can resolve outside the intended `CacheDir` tree, and is then blindly consumed by `os.Rename` in `tryRenameAlternateFile`.

### Finding Description
In `shells/abstract.go`, `newCacheConfig` builds the cache paths: [1](#0-0) 

When `HashCacheKeys` is on, `sanitizer` becomes a no-op (`return s, nil`) because the code assumes "we don't need to sanitize keys anymore" — an assumption that only holds for the *primary* path, since the primary path uses `hashedKey = sha256(humanKey)`: [2](#0-1) 

But the alternate path uses `cacheAlternateKey(humanKey, hashEnabled)`, which — when hashing is enabled — returns `humanKey` **unhashed and unsanitized**: [3](#0-2) [4](#0-3) 

`humanKey` originates directly from the job-controlled `cache:key` (expanded with job variables), with none of the virtual-root protection that is applied to the *default* key (`path.Join("/", jobName, ref)[1:]`): [5](#0-4) 

`getArchivePath` then does `path.Join(build.CacheDir, key, "cache.zip")`, and `path.Join` calls `path.Clean`, which resolves `..` segments lexically — so a key such as `../../../../tmp/evil` collapses the resulting path outside `CacheDir` (this exact traversal-collapsing behavior is demonstrated for the analogous remote-cache-key code path in `cache/cache_test.go`'s `TestGenerateObjectName`, "path traversal but within base path" / "path traversal escapes project namespace" cases). The `functions/concrete/builder/builder.go` `cacheKey` function even documents this exact risk in a comment ("especially under `FF_HASH_CACHE_KEYS=on` where humanKey is used unsanitized"), and `builder_test.go`'s comment states the "bug is invisible" when the FF is off, because `cachekey.Sanitize` normally protects it — confirming the maintainers are aware that hashing mode removes this protection for the default key, but the same unsanitized `humanKey` is also fed into the alternate-file computation in `shells/abstract.go`, which has no equivalent virtual-root guard for user-supplied keys.

The resulting `AlternateArchiveFile` is passed to the `cache-archiver`/`cache-extractor` helper as `--alternate-file`: [6](#0-5) 

`tryRenameAlternateFile` then unconditionally attempts `os.Rename(c.AlternateFile, c.File)` once both `os.Stat` checks pass, with no check that `AlternateFile` is confined to the cache root: [7](#0-6) 

`normalizeArgs` only validates that `--file` is non-empty; it performs no path containment check on either `File` or `AlternateFile`: [8](#0-7) 

### Impact Explanation
The invariant "file operations must stay within intended build/cache/artifact roots" is violated: with `FF_HASH_CACHE_KEYS` on, a pipeline author fully controls the `AlternateFile` argument via `cache:key`, and that argument can point outside `CacheDir`. `tryRenameAlternateFile`'s `os.Rename(c.AlternateFile, c.File)` will then move whatever file exists at the attacker-chosen path into the primary cache slot (which is later uploaded to the shared cache backend under a predictable hashed key), or — on extraction (`cache-extractor`, which shares the same `--alternate-file`/rename-style logic) — move the downloaded cache archive out to the attacker-chosen path. On setups where `CacheDir` (or its parent) is a shared/mounted path across jobs (e.g., distributed local cache directories reused across pipelines on the same runner host), this allows one job to influence file locations belonging to another job's cache scope, rather than being confined to the intended per-key cache root — a concrete violation of cache-root isolation, and a path to leaking cache contents to an attacker-controlled exfiltration path or overwriting files at attacker-chosen locations reachable by traversal from `CacheDir`.

### Likelihood Explanation
Preconditions are simple and job-controlled: `FF_HASH_CACHE_KEYS=true` (a documented, increasingly-recommended feature flag) plus a job setting `cache:key: "../../../../somewhere/evil"` (optionally built from CI variables to obscure intent). No special privileges are needed beyond authoring a `.gitlab-ci.yml`. The bug is deterministic and repeatable every run, since `path.Clean`'s traversal-collapsing is a pure, входит every time `newCacheConfig` runs.

### Recommendation
Apply the same traversal-safe sanitation/virtual-root confinement to the alternate key that is applied to the primary key, regardless of `FF_HASH_CACHE_KEYS` state — i.e., always sanitize/confine `humanKey`/`alternateKey` with `cachekey.Sanitize` (or an equivalent that guarantees the result stays under `CacheDir`) before it is used to build `AlternateArchiveFile`, or verify with `filepath.Rel`/prefix-check that the resolved `AlternateArchiveFile` (and `File`) remain within `build.CacheDir` before ever passing them to `cache-archiver`/`cache-extractor`. Additionally, add a containment check inside `tryRenameAlternateFile`/`normalizeArgs` that rejects `AlternateFile`/`File` values resolving outside the expected cache root as a defense-in-depth measure.

### Proof of Concept
Unit test idea for `shells/abstract_test.go` (`newCacheConfig`):
```go
func TestNewCacheConfig_AlternateFileEscapesCacheDir(t *testing.T) {
    build := &common.Build{
        BuildDir: "/builds",
        CacheDir: "/cache",
        Runner: &common.RunnerConfig{
            RunnerSettings: common.RunnerSettings{
                FeatureFlags: map[string]bool{featureflags.HashCacheKeys: true},
            },
        },
    }
    cc, _, err := newCacheConfig(build, "../../../../tmp/evil")
    require.NoError(t, err)
    // BUG: AlternateArchiveFile resolves outside CacheDir
    assert.True(t, strings.HasPrefix(cc.AlternateArchiveFile, ".."),
        "AlternateArchiveFile escaped CacheDir: %s", cc.AlternateArchiveFile)
}
```
Complementary unit test for `commands/helpers/cache_archiver_test.go`:
```go
func TestTryRenameAlternateFile_RejectsPathOutsideCacheRoot(t *testing.T) {
    dir := t.TempDir()
    outside := filepath.Join(dir, "..", "evil-secret")
    _ = os.WriteFile(outside, []byte("secret"), 0o600)

    c := &CacheArchiverCommand{
        File:          filepath.Join(dir, "cache.zip"),
        AlternateFile: outside,
    }
    c.tryRenameAlternateFile()

    // Expected (fixed) behavior: rename must be rejected, file must remain outside untouched
    assert.FileExists(t, outside)
    assert.NoFileExists(t, c.File)
}
```
Currently this test would fail (the rename succeeds), demonstrating the missing containment check.

### Citations

**File:** shells/abstract.go (L117-126)
```go
// cacheAlternateKey returns the "other" archive key relative to the current FF_HASH_CACHE_KEYS setting.
// When hashing is enabled, the alternate is the unhashed (human-readable) key.
// When hashing is disabled, the alternate is the SHA256-hashed key.
func cacheAlternateKey(humanKey string, hashEnabled bool) string {
	sha256Key := fmt.Sprintf("%x", sha256.Sum256([]byte(humanKey)))
	if hashEnabled {
		return humanKey
	}
	return sha256Key
}
```

**File:** shells/abstract.go (L138-149)
```go
	rawKey := path.Join("/", build.JobInfo.Name, build.GitInfo.Ref)[1:]
	if userKey != "" {
		rawKey = build.GetAllVariables().ExpandValue(userKey)
	}

	hasher := func(s string) string { return s }
	sanitizer := cachekey.Sanitize
	// if hash key support is enabled, we don't need to sanitize keys anymore
	if build.IsFeatureFlagOn(featureflags.HashCacheKeys) {
		hasher = func(s string) string { return fmt.Sprintf("%x", sha256.Sum256([]byte(s))) }
		sanitizer = func(s string) (string, error) { return s, nil }
	}
```

**File:** shells/abstract.go (L171-188)
```go
	hashedKey := hasher(humanKey)

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

	archiveFile, err := getArchivePath(hashedKey)
	if err != nil {
		return nil, warning, err
	}
```

**File:** shells/abstract.go (L190-197)
```go
	// alternateKey is always the "other" naming scheme relative to the current FF setting:
	// - FF ON:  primary=hashed, alternate=unhashed → enables upgrade of old unhashed artifacts
	// - FF OFF: primary=unhashed, alternate=hashed → enables downgrade of old hashed artifacts
	alternateKey := cacheAlternateKey(humanKey, build.IsFeatureFlagOn(featureflags.HashCacheKeys))
	alternateArchiveFile, err := getArchivePath(alternateKey)
	if err != nil {
		return nil, warning, err
	}
```

**File:** commands/helpers/cache_archiver.go (L39-41)
```go
	File                   string   `long:"file" description:"The path to file"`
	AlternateFile          string   `long:"alternate-file" description:"(temporary) Alternate local cache file path (e.g. unhashed name) to rename to --file if --file does not exist"`
	URL                    string   `long:"url" description:"URL of remote cache resource (pre-signed URL)"`
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

**File:** commands/helpers/cache_archiver.go (L338-368)
```go
func (c *CacheArchiverCommand) normalizeArgs() {
	if c.File == "" {
		logrus.Fatalln("Missing --file")
	}

	if c.TransferBufferSize == 0 {
		c.TransferBufferSize = defaultCacheTransferBufferSize
	}
	if c.ChunkSize == 0 {
		c.ChunkSize = defaultCacheChunkSize
	}
	if c.Concurrency == 0 {
		c.Concurrency = defaultCacheConcurrency
	}

	for idx := range c.Paths {
		if path, err := shell.Expand(c.Paths[idx], nil); err != nil {
			logrus.Warnf("invalid path %q: %v", path, err)
		} else {
			c.Paths[idx] = path
		}
	}

	for idx := range c.Exclude {
		if path, err := shell.Expand(c.Exclude[idx], nil); err != nil {
			logrus.Warnf("invalid path %q: %v", path, err)
		} else {
			c.Exclude[idx] = path
		}
	}
}
```
