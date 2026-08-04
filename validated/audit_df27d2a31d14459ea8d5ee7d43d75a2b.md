## Vulnerability Analysis: Restriction Bypass in Cache Key "Protected" Suffix Guard

The reported bug class — a restriction enforced in one function but bypassable through a sibling function that achieves the same effect — has a direct structural analog in GitLab Runner's cache-key handling.

### Title
Restriction on `-protected`-suffixed cache keys is enforced only for `CACHE_FALLBACK_KEY`, not for `.gitlab-ci.yml`'s `cache:fallback_keys`/`cache:key` — ([File: functions/concrete/builder/builder.go])

### Summary
GitLab Runner blocks the `-protected` suffix only when it arrives through the `CACHE_FALLBACK_KEY` predefined variable, but the exact same suffix is accepted with no check whatsoever when supplied via `cache.fallback_keys` (or `cache.key`) declared directly in `.gitlab-ci.yml`.

### Finding Description
In `buildCacheSources`, the fallback-key loop over job-file-declared keys passes no validation callback: [1](#0-0) 

Immediately after, the exact same mechanism (`addSource`) is used for the `CACHE_FALLBACK_KEY` CI/CD variable, but this time with an explicit check that rejects any (expanded) key ending in `-protected`: [2](#0-1) 

The primary `cache.key` path (line 275) similarly has no such check. The `-protected` suffix is not an arbitrary string — it is a security-relevant marker elsewhere in the runner: the Docker executor treats *any* cache key ending in `-protected` (and not `-non_protected`) as reason to mark the whole cache volume as protected, independent of whether the actual git ref is protected: [3](#0-2) 

This is explicitly tied to a GitLab work item on protected-branch cache isolation (`work_items/494478`), and is covered by an integration test that asserts a cache key alone (`blammo-protected`) forces `expectProtectedVolume`, regardless of the ref's protection state: [4](#0-3) 

Given this, the `-protected` suffix functions as a trust boundary marker for cache read/write isolation between protected and unprotected pipelines. The builder explicitly hardens the `CACHE_FALLBACK_KEY` variable path against this suffix (with a comment noting it must check the *expanded* value to avoid bypass via `CACHE_FALLBACK_KEY=$VAR`), which shows the check is a deliberate security control — but the equivalent `.gitlab-ci.yml`-defined `cache.fallback_keys` (and `cache.key`) list reaches the same `addSource` sink with zero checks, exactly mirroring the `withdrawERC20()` vs `withdrawLPToken()` asymmetry in the source report.

### Impact Explanation
A pipeline author (e.g., a contributor pushing to an unprotected branch or opening a merge-request pipeline) can declare:
```yaml
cache:
  key: my-cache
  fallback_keys:
    - my-cache-protected
```
This causes the runner to attempt a cache lookup keyed as `-protected`, bypassing the restriction that exists specifically to stop untrusted CI variables from reaching into the "protected" cache keyspace. Since the underlying S3/GCS/Azure cache adapter path is derived purely from the resolved key string and project ID (no ref-protection check at the storage layer): [5](#0-4) 

an unprotected build could pull cache objects that were only ever intended to be produced/consumed by protected-ref jobs (which may carry more sensitive build outputs, since protected branches often run with elevated CI/CD variables). This is a restriction-bypass leading to unintended cross-trust-boundary cache read access — comparable in class (Medium impact) to the source report.

### Likelihood Explanation
High. No special privilege is required — any contributor who can add or modify `.gitlab-ci.yml` on an unprotected branch/MR (the normal, unprivileged CI-authoring path) can trigger this, whereas the equivalent `CACHE_FALLBACK_KEY` route is explicitly hardened, showing GitLab Runner's own threat model already considers this suffix attacker-relevant.

### Recommendation
Apply the same `-protected` suffix check used for `CACHE_FALLBACK_KEY` (line 323-332) to the `cache.fallback_keys` loop (line 317-319) and to the primary `cache.key` resolution (line 275), or centralize the check inside `addSource`/`cacheKey` so all callers benefit uniformly — mirroring the report's recommendation to move the guard into the shared code path rather than duplicating it per caller.

### Proof of Concept
1. On an unprotected branch/MR pipeline, add to `.gitlab-ci.yml`:
```yaml
job:
  script: ["echo test"]
  cache:
    key: build-cache
    fallback_keys:
      - build-cache-protected
```
2. Run the pipeline. `buildCacheSources` will add `build-cache-protected` as a `CacheSource` with no warning/rejection (unlike the identical value passed via `CACHE_FALLBACK_KEY`, which is dropped with a `"not allowed to end in \"-protected\""` warning, per `TestBuild_CacheExtract_ProtectedFallbackKey` at builder_test.go:857-909).
3. The runner will issue a cache-download attempt against the `-protected`-suffixed key, the same keyspace that `executors/docker/volume.go` treats as belonging to protected-ref caches.

**Caveat:** I could not fully verify from the indexed code whether cache *storage backends* (S3/GCS/Azure adapters) perform any additional protected-ref authorization check beyond key-string matching, since that would depend on bucket/IAM-level configuration outside this repository's scope — the concrete data-leak impact therefore depends on the operator's cache backend configuration, which affects the confirmed severity but not the existence of the code-level restriction-bypass asymmetry itself.

### Citations

**File:** functions/concrete/builder/builder.go (L317-319)
```go
	for _, fk := range cache.FallbackKeys {
		_ = addSource(fk)
	}
```

**File:** functions/concrete/builder/builder.go (L321-333)
```go
	if fk := b.variables.Get("CACHE_FALLBACK_KEY"); fk != "" {
		// Check against humanKey (post-expansion), not the raw value, to prevent bypass via CACHE_FALLBACK_KEY=$VAR.
		_ = addSource(fk, func(humanKey string) bool {
			const blockedSuffix = "-protected"
			if strings.HasSuffix(strings.TrimRight(humanKey, ". "), blockedSuffix) {
				warnings = append(warnings,
					fmt.Sprintf("CACHE_FALLBACK_KEY %q not allowed to end in %q", humanKey, blockedSuffix),
				)
				return false
			}
			return true
		})
	}
```

**File:** executors/docker/volume.go (L11-29)
```go
var createVolumesManager = func(e *executor) (volumes.Manager, error) {
	// Note if any of the cache keys includes the `-protected` suffix (but not the `-non_protected` suffix).
	// See https://gitlab.com/gitlab-org/gitlab/-/work_items/494478.
	protectedKeyIdx := slices.IndexFunc(e.Build.Cache, func(c spec.Cache) bool {
		return strings.HasSuffix(c.Key, "-protected") && !strings.HasSuffix(c.Key, "-non_protected")
	})

	config := volumes.ManagerConfig{
		CacheDir:      e.Config.Docker.CacheDir,
		BasePath:      e.Build.FullProjectDir(),
		UniqueName:    e.Build.ProjectRealUniqueName(),
		TemporaryName: e.getProjectUniqRandomizedName(),
		DisableCache:  e.Config.Docker.DisableCache,
		Driver:        e.Config.Docker.VolumeDriver,
		DriverOpts:    e.Config.Docker.VolumeDriverOps,
		// the volume should be protected if the ref is protected OR if any of the cache volumes have the protected
		// suffix. See https://gitlab.com/gitlab-org/gitlab/-/work_items/494478.
		Protected: e.Build.IsProtected() || protectedKeyIdx >= 0,
	}
```

**File:** executors/docker/docker_command_integration_test.go (L3378-3391)
```go
func Test_CacheVolumeProtected(t *testing.T) {
	test.SkipIfGitLabCIOn(t, test.OSWindows)
	helpers.SkipIntegrationTests(t, "docker", "info")

	tests := map[string]struct {
		protectedRef          bool
		cacheKey              string
		expectProtectedVolume bool
	}{
		"not protected ref, not protected cache key": {false, "blammo", false},
		"not protected ref, non_protected cache key": {false, "blammo-non_protected", false},
		"protected ref, not protected cache key":     {true, "blammo", true},
		"not protected ref, protected cache key":     {false, "blammo-protected", true},
		"protected ref, protected cache key":         {true, "blammo-protected", true},
```

**File:** cache/cache.go (L27-44)
```go
func GetAdapter(config *cacheconfig.Config, timeout time.Duration, shortToken, projectId, key string, sharded bool) Adapter {
	if config == nil {
		return nopAdapter{}
	}

	if key == "" {
		logrus.Warning("Empty cache key. Skipping adapter selection.")
		return nopAdapter{}
	}

	// generate object path
	// runners get their own namespace, unless they're shared, in which case the
	// namespace is empty.
	namespace := ""
	if !config.GetShared() {
		namespace = path.Join("runner", shortToken)
	}
	basePath := path.Join(config.GetPath(), namespace, "project", projectId)
```
