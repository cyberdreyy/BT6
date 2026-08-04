### Title
Unprotected job can force `Protected=true` volume/cache namespace via crafted cache key, colliding with a later protected job's mounted cache - ([File: executors/docker/volume.go])

### Summary
The Docker executor decides whether cache/build volumes are namespaced as "protected" using `e.Build.IsProtected() || protectedKeyIdx >= 0`, where `protectedKeyIdx` is derived purely from a job-supplied `cache.key` string ending in `-protected`. Because this is an OR (not an AND with true ref-protection), an unprivileged pipeline author running a job on an *unprotected* ref/MR can set a cache key ending in `-protected` and force the same `Protected: true` volume-naming path that a genuinely protected-ref job uses, causing both jobs to resolve to the identical host cache directory / Docker volume name for the same destination path.

### Finding Description
`createVolumesManager` in [1](#0-0)  computes:
```
protectedKeyIdx := slices.IndexFunc(e.Build.Cache, func(c spec.Cache) bool {
    return strings.HasSuffix(c.Key, "-protected") && !strings.HasSuffix(c.Key, "-non_protected")
})
...
Protected: e.Build.IsProtected() || protectedKeyIdx >= 0,
```
`e.Build.Cache` (and its `Key` field) comes from the job's `.gitlab-ci.yml` `cache:` configuration — fully attacker-controlled by any pipeline author who can push to an unprotected branch or open an MR. The `Protected` flag is then consumed by `manager.withProtected()` in [2](#0-1)  to append a fixed suffix to the hashed destination path, which is used both for host-based cache directories (`createHostBasedCacheVolume`, [3](#0-2) ) and Docker-volume-based caches (`createCacheVolume`, [4](#0-3) ). `createVolumes` in [5](#0-4)  and `createBuildVolume` invoke this manager to actually mount the resulting path/volume into the job's container.

Since the resulting directory/volume name only depends on: (1) project `UniqueName` (`ProjectRealUniqueName()`), (2) the hashed destination path, and (3) whether `Protected` is true, two jobs in the *same project* with the *same cache destination* and the same crafted `-protected` cache key suffix will resolve to the exact same host path or Docker volume — regardless of whether the ref executing each job is actually protected. An attacker who can run an unprotected pipeline (e.g., open an MR from a fork or a non-protected branch) can pre-seed that shared "protected" cache location with attacker-controlled files (binaries, scripts, `.so` files, config that a later job's build step will consume) before a legitimate protected-ref pipeline runs and mounts the same path, expecting it to be trusted state exclusive to protected refs.

No other check (e.g., verifying the ref is actually protected before allowing the `-protected` cache key suffix to take effect) exists in this path — `createVolumes`/`createVolumesManager` never re-validates the cache-key-derived protection claim against `e.Build.IsProtected()` before using it to select shared storage.

### Impact Explanation
This breaks the stated invariant "mounted state must be cleared or re-created when the protection boundary changes." An unprivileged pipeline author can plant attacker-controlled files into the protected-cache-namespaced host directory or Docker volume, which is later mounted, unmodified, into a subsequent job running on an actually protected ref/branch — a protected-job escalation via mounted-state reuse/poisoning, matching the scoped Immunefi impact exactly.

### Likelihood Explanation
Feasible and repeatable: it only requires (1) push/MR access to trigger an unprotected pipeline in the same project, (2) ability to set `cache: key:` in `.gitlab-ci.yml` (a completely standard, unprivileged CI config field) with a `-protected` suffix, and (3) knowledge (or brute-forceable guessing, since it's an MD5 hash of a project-relative cache path such as `/builds/<group>/<project>/cache` — often predictable/default) of the destination path used by the target protected job's cache. No admin, secrets, or race conditions are required; it is deterministic given matching cache key/destination.

### Recommendation
Only allow the `-protected` cache-key suffix to elevate volume naming to `Protected: true` when the ref is *actually* protected (`e.Build.IsProtected()`), i.e., use `Protected: e.Build.IsProtected()` alone, or `IsProtected() && protectedKeyIdx >= 0` combined with a separate mechanism, rather than an unconditional OR that lets an unprotected job's cache key alone force the protected namespace. Additionally, consider incorporating ref-protection state or a server-issued unforgeable token into the hash/name so unprotected jobs cannot compute the same destination-derived name as a protected job even if they guess the cache key.

### Proof of Concept
Go integration test outline (extending `manager_test.go`/`docker_test.go`):
1. Build A: unprotected ref, `Cache: [{Key: "foo-protected"}]`, destination `/builds/project/cache`. Call `createVolumesManager` + `createVolumes`; assert resulting volume/dir name equals `withProtected(hashPath("/builds/project/cache"))` with the protected suffix, even though `IsProtected()==false`.
2. Build B: protected ref (`IsProtected()==true`), same project, same destination `/builds/project/cache`, no special cache key needed. Call `createVolumesManager` + `createVolumes`.
3. Assert the volume/host-directory name computed in step 1 equals the one computed in step 2 (`assert.Equal(t, nameA, nameB)`), proving cross-protection-boundary collision.
4. For host-based cache, additionally write a marker file into the host path from Build A's mount, then run Build B's container and assert the marker file is visible/mounted — demonstrating that attacker-controlled state written by an unprotected job is inherited unmodified by a protected job.

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

**File:** executors/docker/internal/volumes/manager.go (L169-192)
```go
func (m *manager) createHostBasedCacheVolume(destination string) error {
	destination, err := m.absolutePath(destination)
	if err != nil {
		return fmt.Errorf("defining absolute path: %w", err)
	}

	err = m.managedVolumes.Add(destination)
	if err != nil {
		return fmt.Errorf("updating managed volumes list: %w", err)
	}

	// The leaf directory dir has a name with a length of:
	//	- 42 chars when protected
	//	- 32 chars when not protected (the length of the md5sum only)
	dir := m.withProtected(hashPath(destination))
	hostPath := m.parser.Path().Join(m.config.CacheDir, m.config.UniqueName, dir)

	m.appendVolumeBind(&parser.Volume{
		Source:      hostPath,
		Destination: destination,
	})

	return nil
}
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

**File:** executors/docker/docker.go (L1579-1595)
```go
func (e *executor) createVolumes() error {
	e.SetCurrentStage(ExecutorStageCreatingUserVolumes)
	e.BuildLogger.Debugln("Creating user-defined volumes...")

	if e.volumesManager == nil {
		return errVolumesManagerUndefined
	}

	for _, volume := range e.Config.Docker.Volumes {
		err := e.volumesManager.Create(e.Context, volume)
		if err != nil {
			return err
		}
	}

	return nil
}
```
