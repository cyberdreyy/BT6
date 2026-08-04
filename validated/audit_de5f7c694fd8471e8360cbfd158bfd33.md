### Title
Docker cache volume can be forced into the "protected" volume namespace via an attacker-controlled `cache:key` suffix - (File: executors/docker/volume.go)

### Summary
`createVolumesManager` in `executors/docker/volume.go` marks a Docker executor's persistent cache volumes as "protected" if *any* job cache key ends in `-protected`, and this key comes straight from the job's `.gitlab-ci.yml` `cache: key:` field with no validation that the branch is actually protected. Because Docker volume names for persistent storage (`executors/docker/internal/volumes/manager.go`) are derived only from the project's `UniqueName` and a hash of the mount destination (not the cache key), an unprivileged pipeline author on an unprotected branch/MR can forge the same volume name/namespace normally reserved for protected-ref jobs, causing their job to read from and write to the same Docker volume that protected-branch jobs use.

### Finding Description
`createVolumesManager` computes:
```go
protectedKeyIdx := slices.IndexFunc(e.Build.Cache, func(c spec.Cache) bool {
    return strings.HasSuffix(c.Key, "-protected") && !strings.HasSuffix(c.Key, "-non_protected")
})
...
Protected: e.Build.IsProtected() || protectedKeyIdx >= 0,
``` [1](#0-0) 

`c.Key` is the raw/expanded `cache: key:` string from the job's own `.gitlab-ci.yml`, fully attacker-controlled by anyone who can author a pipeline (e.g., in a merge request from an unprotected branch/fork). GitLab does not sanitize or gate this suffix server-side before handing it to the runner.

This `Protected` flag flows into `ManagerConfig.Protected`, which `withProtected` uses to append `-protected` to the Docker volume name for *every* persistent cache volume of the job: [2](#0-1) 

Critically, the volume name itself does **not** incorporate the cache key content — only `m.config.UniqueName` (project-scoped) and a hash of the mount destination path: [3](#0-2) 

So a legitimate protected-ref job with `volumes = ["/cache"]` produces `Uniq-cache-<hash(/cache)>-protected`, and an unprotected-ref job with the *same* destination normally produces `Uniq-cache-<hash(/cache)>` (no suffix) — two disjoint volumes, which is the entire point of work item 494478 (to stop protected- and unprotected-ref jobs from sharing cache state). But by simply declaring a cache entry with `key: "anything-protected"`, an attacker on an unprotected ref flips `Protected` to `true` for their own job, producing the exact same volume name (`Uniq-cache-<hash(/cache)>-protected`) that protected-ref jobs use for the same project/runner. Docker will then either create that volume (if it doesn't exist yet) or reuse the existing one (if protected jobs already ran), giving the attacker direct read/write access to data that is supposed to be exclusive to protected-ref pipelines.

This is the same attack class GitLab already defends against in a different code path — `buildCacheSources`/`extractCacheOrFallbackCachesWrapper` explicitly reject a `CACHE_FALLBACK_KEY` ending in `-protected` (`functions/concrete/builder/builder.go:321-333`, `shells/abstract.go:319-329`) — but no equivalent check exists for the primary `cache.Key` value used by `createVolumesManager` in the Docker executor.

### Impact Explanation
An unprivileged pipeline author (MR/branch pipeline on an unprotected ref) can cause their job's Docker persistent cache volume to alias with the volume used by protected-ref jobs of the same project. This breaks the intended isolation between protected and unprotected pipelines' cache state: the attacker can plant/modify files in the shared volume that will later be mounted, unmodified, into a protected-ref job (e.g. a deploy job with access to protected CI/CD variables), potentially leading to code execution or data exfiltration in that protected job's context. It can also let the attacker read cached data (build outputs, credentials cached to disk, etc.) written there by earlier protected-ref jobs.

### Likelihood Explanation
Trivial to trigger: any pipeline author with permission to modify `.gitlab-ci.yml` in a merge request (an unprotected ref) can add a `cache: key: "x-protected"` entry. No special runner configuration is required beyond the Docker executor having a `volumes` entry configured (a very common setup), and requires a protected-ref job to have run first (or run afterward) mounting the same destination path for the volume to actually be shared. Preconditions are common in typical GitLab.com/self-managed Docker executor configurations that use persistent cache volumes without `cache_dir` host paths.

### Recommendation
Do not let the raw job-supplied `cache.Key` influence Docker volume protection. Either:
- Remove the `protectedKeyIdx` fallback entirely and rely solely on `e.Build.IsProtected()`, or
- If the fallback is required for some legitimate flow, validate that the "-protected" suffix only originates from a trusted, server-computed source (not raw pipeline YAML), mirroring the `blockProtectedFallback`/`addSource` guard already used for `CACHE_FALLBACK_KEY` in `functions/concrete/builder/builder.go` and `shells/abstract.go`.

### Proof of Concept
Go integration test (extends existing `Test_CacheVolumeProtected` in `executors/docker/docker_command_integration_test.go`):
1. Run job A with `GitInfo.Protected = true`, `Volumes = ["/cache"]`, no cache key — confirms volume `Uniq-cache-<hash>-protected` is created and write a marker file to `/cache/marker`.
2. Run job B with `GitInfo.Protected = false` (unprotected ref), same project (`ProjectID`), `Volumes = ["/cache"]`, and `Cache = [{Key: "x-protected", Paths: ["cached/*"]}]`.
3. Assert that job B's created/attached volume name equals `Uniq-cache-<hash>-protected` (same as job A's), and that `/cache/marker` is visible/writable inside job B's container — proving cross-protection-boundary volume reuse triggered purely by an attacker-chosen cache key string.

### Citations

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

**File:** executors/docker/internal/volumes/manager.go (L209-218)
```go
	hashedDestination := hashPath(destination)
	name := m.config.TemporaryName
	if reusable {
		name = m.config.UniqueName
	}

	// volumeName might get quite long. Docker is however happy to create volumes with long names. There is the "myth"
	// that volume names are treated like DNS labels, and thus only allow a length of 63 chars, however that does not hold
	// true. In fact, we already create way longer names, and would catch those issues in various integration tests.
	volumeName := m.withProtected(fmt.Sprintf("%s-cache-%s", name, hashedDestination))
```

**File:** executors/docker/internal/volumes/manager.go (L291-299)
```go
// withProtected returns a string with a specific suffix when the config states, we are running against a protected
// ref, or when any of the cache keys includes the `-protected` suffix.
// See https://gitlab.com/gitlab-org/gitlab/-/work_items/494478.
func (m *manager) withProtected(s string) string {
	if !m.config.Protected {
		return s
	}
	return s + protectedSuffix
}
```
