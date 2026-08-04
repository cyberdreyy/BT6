### Title
Cache key content controls volume-protection classification, enabling cross-branch cache volume name collision - ([File: executors/docker/volume.go], [File: executors/docker/internal/volumes/manager.go])

### Summary
`createVolumesManager` derives `ManagerConfig.Protected` not only from `e.Build.IsProtected()` but also from whether any `e.Build.Cache[].Key` ends in `-protected` (and not `-non_protected`). Because cache Docker volume names are deterministic (`UniqueName-cache-hash(destination)[-protected]`, where `UniqueName` is the project's real unique name, not ref-specific), a pipeline author on an unprotected branch can force `Protected=true` purely via a job-supplied cache key string and cause Runner to compute the exact same volume name/labels that a genuine protected-ref job in the same project would use.

### Finding Description
In `executors/docker/volume.go`, `createVolumesManager` computes:
```go
protectedKeyIdx := slices.IndexFunc(e.Build.Cache, func(c spec.Cache) bool {
    return strings.HasSuffix(c.Key, "-protected") && !strings.HasSuffix(c.Key, "-non_protected")
})
...
Protected: e.Build.IsProtected() || protectedKeyIdx >= 0,
``` [1](#0-0) 

`e.Build.Cache[].Key` is a value under pipeline/job author control (the `cache:key` field in `.gitlab-ci.yml`, or dynamic values built from CI variables the job can influence). Thus an unprivileged pipeline author on a non-protected branch can set `cache: {key: "foo-protected"}` and force `Protected=true` for their job's volume manager, with no actual ref protection.

That `Protected` flag then flows into `executors/docker/internal/volumes/manager.go`'s `createCacheVolume`, where the volume name is built as:
```go
volumeName := m.withProtected(fmt.Sprintf("%s-cache-%s", name, hashedDestination))
```
with `name = m.config.UniqueName` (i.e. `e.Build.ProjectRealUniqueName()`, a per-project — not per-ref — identifier) for reusable/persistent caches, and `withProtected` simply appends the literal `-protected` suffix when `m.config.Protected` is true:
```go
func (m *manager) withProtected(s string) string {
    if !m.config.Protected {
        return s
    }
    return s + protectedSuffix
}
``` [2](#0-1) [3](#0-2) 

Because `UniqueName` and `hashedDestination` depend only on the project and cache path (not the branch/ref), the final volume name for a persistent cache under a given project + cache path is **identical** whether it is computed from an actually-protected ref or from an unprotected-ref job whose cache key merely contains the `-protected` substring. There is no check anywhere in `createVolumesManager`, `createCacheVolume`, or `withProtected` that ties the `-protected` volume name/label back to genuine ref protection state once the attacker-controlled key match sets `Protected=true`. Existing safeguards (allowed-image checks, path validation, masking) are irrelevant here — the vulnerable logic is purely the protection classification itself, which conflates "cache key text supplied by the job" with "trust level of the ref."

### Impact Explanation
Since Docker named volumes for reusable caches persist across job runs on the same runner host and are keyed by `UniqueName-cache-hash(destination)[-protected]`, a job on an unprotected branch (e.g., a fork MR or feature branch) can deliberately name its own cache key with the `-protected` suffix to mount the exact same Docker volume that a job on a protected branch would use for the same cache path. This lets the unprotected/attacker job read the contents of the protected-branch cache (potential exposure of protected-only build artifacts/dependencies staged in cache) and/or write to it, poisoning what the protected-branch pipeline will later consume — a genuine cross-job/cross-trust-boundary cache volume collision, scoped to the same project and cache path.

### Likelihood Explanation
Preconditions: shared-host docker executor with persistent (non-DisableCache, no host CacheDir) cache volumes reused across jobs of the same project; the attacker only needs push/MR access to trigger a pipeline on a non-protected ref and control over `.gitlab-ci.yml`'s `cache: key` field (or a CI variable feeding it) for a cache path also used by protected-branch jobs. This is a low-effort, fully repeatable action requiring no privileged access — merely crafting a cache key string.

### Recommendation
Remove the ability for job-supplied cache key text to influence the `Protected` classification used for volume naming/labeling. Protection status should be derived solely from `e.Build.IsProtected()` (actual ref protection), never from pattern-matching on `Cache[].Key`. If the "-protected" cache-key override behavior is intentionally desired for some purpose, it must not be allowed to widen the produced volume name to alias with a genuinely protected-ref job's persistent volume — e.g., by binding the volume name to the actual ref protection state independently of the cache key text, or by rejecting/sanitizing cache keys that contain the reserved `-protected`/`-non_protected` suffixes when the ref itself is not protected.

### Proof of Concept
Go unit test in `executors/docker/volume_test.go` (or extending `manager_test.go`'s `Test_CacheVolumeProtected`-style test):
```go
func TestCacheKeySuffixForcesProtectedVolumeCollision(t *testing.T) {
    // Job A: genuinely protected ref, no special cache key
    buildA := &common.Build{ /* ... */ }
    buildA.JobResponse.GitInfo.Ref = "protected-branch"
    // IsProtected() -> true
    cfgA := computeManagerConfig(buildA) // via createVolumesManager logic
    nameA := (&manager{config: cfgA}).withProtected(fmt.Sprintf("%s-cache-%s", cfgA.UniqueName, hashPath("/cache/path")))

    // Job B: unprotected ref, attacker sets cache key suffix "-protected"
    buildB := &common.Build{ /* same project, same UniqueName */ }
    buildB.JobResponse.GitInfo.Ref = "feature-branch" // IsProtected() -> false
    buildB.Cache = []spec.Cache{{Key: "foo-protected"}}
    cfgB := computeManagerConfig(buildB)
    nameB := (&manager{config: cfgB}).withProtected(fmt.Sprintf("%s-cache-%s", cfgB.UniqueName, hashPath("/cache/path")))

    assert.True(t, cfgB.Protected) // forced true despite unprotected ref
    assert.Equal(t, nameA, nameB)  // volume names collide -> proves namespace collision
}
```
Expected assertions: `cfgB.Protected == true` even though the ref is unprotected, and `nameA == nameB`, demonstrating that the unprotected-ref job computes the identical Docker volume name (and `protected=true` label) that the genuinely protected-ref job uses, confirming the cache-volume collision/poisoning path.

### Citations

**File:** executors/docker/volume.go (L12-28)
```go
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
```

**File:** executors/docker/internal/volumes/manager.go (L194-251)
```go
func (m *manager) createCacheVolume(
	ctx context.Context,
	destination string,
	reusable bool,
) (string, error) {
	destination, err := m.absolutePath(destination)
	if err != nil {
		return "", fmt.Errorf("defining absolute path: %w", err)
	}

	err = m.managedVolumes.Add(destination)
	if err != nil {
		return "", fmt.Errorf("updating managed volumes list: %w", err)
	}

	hashedDestination := hashPath(destination)
	name := m.config.TemporaryName
	if reusable {
		name = m.config.UniqueName
	}

	// volumeName might get quite long. Docker is however happy to create volumes with long names. There is the "myth"
	// that volume names are treated like DNS labels, and thus only allow a length of 63 chars, however that does not hold
	// true. In fact, we already create way longer names, and would catch those issues in various integration tests.
	volumeName := m.withProtected(fmt.Sprintf("%s-cache-%s", name, hashedDestination))

	vBody := client.VolumeCreateOptions{
		Name:       volumeName,
		Driver:     m.config.Driver,
		DriverOpts: m.config.DriverOpts,
		Labels: m.labeler.Labels(map[string]string{
			"destination": destination,
			"protected":   strconv.FormatBool(m.config.Protected),
			"reusable":    strconv.FormatBool(reusable),
			"type":        "cache",
		}),
	}

	v, err := m.client.VolumeCreate(ctx, vBody)
	if err != nil {
		return "", fmt.Errorf("creating docker volume: %w", err)
	}

	if m.permissionSetter != nil {
		err = m.permissionSetter.Set(ctx, v.Name, m.labeler.Labels(map[string]string{"type": "cache-init"}))
		if err != nil {
			return "", fmt.Errorf("set volume permissions: %w", err)
		}
	}

	m.appendVolumeBind(&parser.Volume{
		Source:      v.Name,
		Destination: destination,
	})
	m.logger.Debugln(fmt.Sprintf("Using volume %q as cache %q...", v.Name, destination))

	return volumeName, nil
}
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
