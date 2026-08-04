### Title
Cache-key-controlled `Protected` override lets an unprotected-ref job collide with/poison the protected-ref cache volume - (executors/docker/volume.go)

### Summary
The `Protected` flag used to compute cache volume names (`withProtected` suffix) is not derived solely from the ref's actual protection status — it is also set to `true` whenever any `Cache.Key` in `.gitlab-ci.yml` ends in `-protected` (and not `-non_protected`). Because the resulting Docker volume name depends only on the project's `UniqueName` and a hash of the cache destination path (never on the branch or the cache key itself), an unprivileged pipeline author on an unprotected ref can force their job to compute the exact same volume name that a legitimately protected-ref job would use, causing Docker to reuse (not recreate) that volume.

### Finding Description
`createVolumesManager` computes: [1](#0-0) 
`protectedKeyIdx` scans `e.Build.Cache` (attacker-controlled via `.gitlab-ci.yml` `cache.key`) for any key ending in `-protected`, and ORs that into `config.Protected` alongside the real ref-protection check.

Volume names are computed in `createCacheVolume`: [2](#0-1) 
`volumeName := m.withProtected(fmt.Sprintf("%s-cache-%s", name, hashedDestination))`. Critically, `hashedDestination` is `hashPath(destination)` — an MD5 of the cache *destination path* configured in `.gitlab-ci.yml` (e.g. `node_modules`), and `name` is `m.config.UniqueName` (`e.Build.ProjectRealUniqueName()`), which is stable per-project and does not vary by branch/ref. The `Cache.Key` string itself is never mixed into the volume name; it only flips the boolean via `protectedKeyIdx`.

`withProtected` simply appends `-protected` when the boolean is true: [3](#0-2) 

Exploit flow:
1. Attacker (unprivileged contributor/MR author) controls `.gitlab-ci.yml` on an unprotected/MR-source branch, e.g. via a merge-request pipeline that checks out the MR's own CI config.
2. Attacker sets `cache.key: foo-protected` for a cache whose `paths` produce the same destination directory used by the project's normal protected-branch cache (commonly the default/implicit cache destination, or an easily-guessed one such as `node_modules`, `.cache`, etc.).
3. `protectedKeyIdx >= 0` becomes true → `config.Protected = true` for the attacker's unprotected-ref build, even though `e.Build.IsProtected()` is false.
4. `createCacheVolume` computes `volumeName = UniqueName-cache-hash(destination)-protected` — identical to the name a genuine protected-ref job for the same project/destination would compute.
5. `m.client.VolumeCreate` is called with that name. Docker's volume-create API is idempotent by name: if the volume already exists (created earlier by a real protected pipeline run), it is returned unchanged rather than recreated.
6. The attacker's (potentially malicious) container is then bind-mounted onto that pre-existing "protected" volume with full read/write access, and `permissionSetter.Set` (cache-init `chmod 0777`) is invoked against it, as it is for every cache volume creation.
7. Because the mount is read/write, the attacker's job can write arbitrary/malicious content into that volume. Any subsequent job on a genuinely protected ref that reuses the same volume name will restore/extract the attacker's injected content — a cache-poisoning path that persists across pipeline runs and job cancellations, since Docker named volumes are not torn down between jobs (`reusable=true` path).

Existing checks (allowed-image checks, overwrite guards, path validation) do not intervene here: `VolumeCreate`/`addCacheVolume` never validates that the `Protected` flag genuinely corresponds to `e.Build.IsProtected()` before allowing an override via `Cache.Key`, and there's no separate namespacing (e.g., embedding ref-protection provenance or project visibility) preventing an unprotected job from computing a protected volume's name.

### Impact Explanation
An unprivileged actor who can only submit a merge request (and thus control its own unprotected-ref `.gitlab-ci.yml`) can cause their build to attach to, and write into, the same Docker volume that protected-branch builds of the same project rely on for cache. This breaks the isolation invariant that the `-protected` suffix mechanism (work item 494478) was designed to establish, enabling persistent cache poisoning/content substitution affecting future protected-branch builds on the same runner/host — a cross-branch cache integrity violation that can persist across job cancellations and multiple pipeline runs until the poisoned volume is manually removed.

### Likelihood Explanation
Preconditions are realistic and commonly met: shared Docker volume cache (`DisableCache=false`, no `CacheDir` host-based cache), and the attacker only needs write access to `.gitlab-ci.yml` on a non-protected branch (e.g., a fork-based MR pipeline) — no elevated GitLab or runner privileges required. Guessing/matching the destination path is straightforward since cache `paths` are typically conventional (`node_modules`, `vendor`, `.cache`, default build dir), and `ProjectRealUniqueName` is deterministic per project. The bug is fully deterministic and repeatable — no timing or race conditions needed.

### Recommendation
Do not allow `Cache.Key` content to elevate the `Protected` designation used for volume naming/isolation. Either remove the `protectedKeyIdx` override entirely and derive `Protected` solely from `e.Build.IsProtected()`, or, if an opt-in "extra isolation" mode is desired, use a distinct, one-directional naming scheme (e.g., only allow opting into a *more* isolated/private volume namespace tied also to the job/pipeline ID, never one that can collide with the name computed for genuinely protected refs). At minimum, incorporate the true ref-protection status into the volume name computation so that an unprotected-ref job can never compute the same name as a protected-ref job for the same project/destination.

### Proof of Concept
Go unit test in `executors/docker/internal/volumes/manager_test.go` style:
```go
func TestProtectedKeyOverrideCollidesWithRealProtectedVolume(t *testing.T) {
    destination := "/builds/project/node_modules"

    // Simulate a genuine protected-ref build.
    cfgProtectedRef := ManagerConfig{UniqueName: "proj123", Protected: true}
    mProtectedRef := &manager{config: cfgProtectedRef, parser: ..., labeler: ...}
    nameFromRealProtectedRef := mProtectedRef.withProtected(fmt.Sprintf("%s-cache-%s", cfgProtectedRef.UniqueName, hashPath(destination)))

    // Simulate an unprotected-ref build where attacker sets cache.key ending in "-protected".
    protectedKeyIdx := 0 // attacker-controlled cache key "foo-protected"
    cfgAttacker := ManagerConfig{UniqueName: "proj123", Protected: false || (protectedKeyIdx >= 0)}
    mAttacker := &manager{config: cfgAttacker, parser: ..., labeler: ...}
    nameFromAttacker := mAttacker.withProtected(fmt.Sprintf("%s-cache-%s", cfgAttacker.UniqueName, hashPath(destination)))

    // BUG: these should never be equal for a build where IsProtected() == false,
    // but they are, because Protected can be forced via Cache.Key.
    assert.Equal(t, nameFromRealProtectedRef, nameFromAttacker,
        "unprotected-ref job computed the same volume name as a protected-ref job")
}
```
Integration-level PoC job plan:
1. Run pipeline A on a protected branch with default cache `paths: [node_modules]`; let it populate the cache volume (`proj-cache-<hash>-protected`).
2. Run pipeline B on an unprotected MR branch with `cache: {key: "foo-protected", paths: [node_modules]}`.
3. Assert (via `docker volume inspect`) that pipeline B mounts the identical volume name/ID created in step 1, and that content written by pipeline B's job is visible in pipeline A's subsequent run — proving cross-branch cache write access.

### Citations

**File:** executors/docker/volume.go (L14-28)
```go
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

**File:** executors/docker/internal/volumes/manager.go (L294-299)
```go
func (m *manager) withProtected(s string) string {
	if !m.config.Protected {
		return s
	}
	return s + protectedSuffix
}
```
