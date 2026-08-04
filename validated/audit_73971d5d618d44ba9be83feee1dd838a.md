### Title
`cacheOptions.FallbackKeys` entries bypass the `-protected` suffix guard entirely - ([File: shells/abstract.go])

### Summary
`extractCacheOrFallbackCachesWrapper` only applies the `blockProtectedFallback` suffix check to the singular `CACHE_FALLBACK_KEY` variable, but the `cacheOptions.FallbackKeys` list (populated from the `.gitlab-ci.yml` `cache:fallback_keys` config) is added with no key checks at all. A job that controls its cache `fallback_keys` list can therefore directly reference a `*-protected` cache key and have Runner attempt to pull it, with zero suffix filtering.

### Finding Description
In `extractCacheOrFallbackCachesWrapper` (`shells/abstract.go`), two independent fallback sources are folded into `cacheConfigs`: [1](#0-0) 

```go
// the fallback cache keys from the cache config
for _, cacheKey := range cacheOptions.FallbackKeys {
    addCacheConfig(buildVars.ExpandValue(cacheKey))
}
```

This call passes no `keyChecks` to `addCacheConfig`/`newCacheConfig`. Immediately after, the `CACHE_FALLBACK_KEY` variable is added with the `blockProtectedFallback` guard: [2](#0-1) 

`blockProtectedFallback` is the *only* enforcement point for the `-protected` naming convention, and it is wired exclusively to the `CACHE_FALLBACK_KEY` path. Since `cacheOptions.FallbackKeys` is populated straight from the job's cache configuration (`spec.Cache.FallbackKeys`, expandable via CI variables) and is fully attacker/job-controlled per the stated preconditions, an unprotected job can set:

```yaml
cache:
  fallback_keys:
    - "main-protected"
```

and the runner will call `addCacheConfig("main-protected")` → `newCacheConfig` → produce a `cacheConfig` for that exact key with no suffix check applied → `addExtractCacheCommand` will attempt to download it via `cache-extractor`, exactly like any other cache key.

Regarding the sub-question about fuzzing `blockProtectedFallback` itself (trailing whitespace/unicode tricks): that specific bypass is not exploitable. The check is invoked from inside `newCacheConfig` on `humanKey` — i.e., the *same* string used to compute `HashedKey`/`ArchiveFile` (identity function when `FF_HASH_CACHE_KEYS` is on, sanitized value otherwise). Any manipulation (extra suffix characters, unicode whitespace not stripped by `strings.TrimRight(key, ". ")`) that causes `HasSuffix` to miss the `-protected` suffix necessarily also changes the literal key/hash used for the cache lookup, so it can never resolve to byte-identical storage backend object as an actual `*-protected` cache. Thus fuzzing the trim/suffix logic in isolation does not yield cross-project cache access.

The real gap is architectural: the `-protected` convention is enforced on one fallback source and not the other.

### Impact Explanation
An unprotected job (e.g., running on a non-protected feature branch/MR pipeline) can configure `cache:fallback_keys` to name a real protected-branch cache key and have Runner fetch it — bypassing the intended "protected caches are never reachable as fallback for unprotected jobs" invariant. This is a cross-branch/cross-project cache read of potentially secret-bearing artifacts, matching the scoped impact.

### Likelihood Explanation
Highly feasible: any job author who can edit `.gitlab-ci.yml` (or influence its variables) for an unprotected ref can set `fallback_keys` to a value they merely need to guess or know (e.g., convention-based names like `<branch>-protected` or `main-protected`), no special privileges required beyond normal pipeline authorship on an unprotected ref. It's fully repeatable and requires no timing/race conditions.

### Recommendation
Apply the same `blockProtectedFallback` (or an equivalent, hardened check applied to the exact key used for lookup — not just a suffix heuristic) uniformly to every fallback source: both `cacheOptions.FallbackKeys` entries and `CACHE_FALLBACK_KEY`. Ideally, protection status should be tracked as an explicit property of the cache config (e.g., derived from the job's `protected` flag when the cache was written) rather than inferred from a string suffix convention, since naming conventions are inherently spoofable/guessable.

### Proof of Concept
Go unit test added to `shells/abstract_test.go`:

```go
func TestExtractCacheOrFallbackCaches_FallbackKeysBypassProtectedGuard(t *testing.T) {
    build := ... // build for an *unprotected* job
    cacheOptions := spec.Cache{
        Key:           "unprotected-job-key",
        FallbackKeys:  []string{"main-protected"}, // matches naming convention of a real protected cache
    }
    w := &mock ShellWriter{}
    info := common.ShellScriptInfo{Build: build, RunnerCommand: "gitlab-runner"}

    initialCC, _, _ := newCacheConfig(build, cacheOptions.Key)
    (&AbstractShell{}).extractCacheOrFallbackCachesWrapper(context.Background(), w, info, *initialCC, cacheOptions)

    // Assert: no "not allowed to end in" warning was emitted for "main-protected"
    assert.NotContains(t, w.warnings, `not allowed to end in "-protected"`)
    // Assert: cache-extractor command args reference the hashed/human key for "main-protected"
    assert.Contains(t, strings.Join(w.commands, " "), expectedArchivePathFor("main-protected"))
}
```

Expected assertion failure demonstrates that `main-protected` is added as a fallback cache config and passed to `addExtractCacheCommand` without any protected-suffix warning/rejection, confirming the bypass.

### Citations

**File:** shells/abstract.go (L314-317)
```go
	// the fallback cache keys from the cache config
	for _, cacheKey := range cacheOptions.FallbackKeys {
		addCacheConfig(buildVars.ExpandValue(cacheKey))
	}
```

**File:** shells/abstract.go (L319-329)
```go
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
