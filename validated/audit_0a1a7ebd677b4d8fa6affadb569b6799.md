### Title
Cache key suffix `-protected` lets an unprotected-ref job self-elevate into the protected cache volume namespace - (File: executors/docker/volume.go)

### Summary
`createVolumesManager` computes `config.Protected` as `e.Build.IsProtected() || protectedKeyIdx >= 0`, where `protectedKeyIdx` is derived purely from the string suffix of an attacker-controlled `cache.key` value in `.gitlab-ci.yml`. This lets an unprotected-ref pipeline force the volume manager to treat its cache volumes as "protected," producing the exact same `withProtected(hashPath(destination))`-derived volume name / host path that a genuinely protected-ref pipeline would produce for the same cache destination.

### Finding Description
`createVolumesManager` (executors/docker/volume.go:14-28) computes: [1](#0-0) [2](#0-1) 

`protectedKeyIdx` only inspects `e.Build.Cache[].Key`, a field fully controlled by whoever writes `.gitlab-ci.yml` for that pipeline (including on unprotected branches/MRs). It is entirely decoupled from `e.Build.IsProtected()`, which is the only value derived from server-verified `GitInfo.Protected`. The `||` combination means either condition alone sets `config.Protected = true`.

That `Protected` flag then flows into `manager.withProtected` (executors/docker/internal/volumes/manager.go:294-299), which appends the `-protected` suffix to any cache volume/name/host-path built via `createCacheVolume` (line 218) or `createHostBasedCacheVolume` (line 183), both of which derive the volume identity from `hashPath(destination)` — the cache mount **destination**, not the cache key and not the branch. Since `UniqueName` (`e.Build.ProjectRealUniqueName()`) is scoped per-project, not per-branch, two pipelines from the same project — one on a protected ref, one on an unprotected ref — that cache the same destination directory will compute an identical `hashPath(destination)`. If the unprotected pipeline's author simply sets any `cache.key` ending in `-protected` (and not `-non_protected`), `withProtected` will append the same suffix that a legitimate protected-ref pipeline gets automatically via `IsProtected()==true`, producing the same volume name (Docker-volume mode) or the same host directory (`CacheDir/UniqueName/hash-protected`, host-based mode).

No check ties the `-protected` cache-key convention to an actually-protected ref or to any authorization decision — it is a pure string match on user-supplied YAML content.

### Impact Explanation
An unprivileged pipeline author on an unprotected branch/MR can, by naming a cache key with a `-protected` suffix and caching the same paths used by protected pipelines, cause their cache volume/mount to alias the volume/host-directory used by protected-ref pipelines of the same project. This breaks the invariant "unprotected-branch jobs cannot read protected-branch cache volumes": it allows disclosure of protected-branch cache contents to an unprotected job, and — if the unprotected job has cache push permission — poisoning of that shared cache for future protected-branch jobs (supply-chain risk, since protected pipelines often build/deploy trusted artifacts).

### Likelihood Explanation
Preconditions: shared `Docker.CacheDir` (host-based caching) or a shared Docker daemon/volume namespace across runners servicing both protected and unprotected pipelines of the same project, and the attacker being able to submit/modify `.gitlab-ci.yml` on an unprotected ref (a very common capability — e.g. any contributor opening an MR). No leaked secrets or admin access are required; the trigger is a one-line cache-key config change. This is highly feasible and fully repeatable.

### Recommendation
Do not let the `-protected` cache-key convention unilaterally set `config.Protected = true` for unprotected refs. Either: (1) only honor the `-protected` key suffix when `e.Build.IsProtected()` is already true (i.e., use it to *opt out* of protected suffixing via `-non_protected`, never to opt *in* from an unprotected context), or (2) additionally namespace cache volume/host paths by ref-protection status (e.g., incorporate `IsProtected()` into `hashPath`/`UniqueName`) so an attacker-chosen key string can never cause a path/name collision with genuinely protected caches.

### Proof of Concept
Go test in `executors/docker/volume_test.go` (or extend existing manager tests):
```go
func TestCreateVolumesManager_UnprotectedRefWithProtectedCacheKey(t *testing.T) {
    build := &common.Build{
        JobResponse: common.JobResponse{
            GitInfo: common.GitInfo{Protected: false}, // unprotected ref
        },
    }
    build.Cache = []spec.Cache{{Key: "mykey-protected", Paths: []string{"build/"}}}

    e := &executor{Build: build /* ... other required fields ... */}
    vm, err := createVolumesManager(e)
    require.NoError(t, err)

    // Same destination as would be used by a protected-ref pipeline caching "build/"
    err = vm.Create(context.Background(), "/builds/project/build")
    require.NoError(t, err)

    binds := vm.Binds()
    // Assert the produced volume/host path ends with the "-protected" suffix
    // even though GitInfo.Protected == false, proving path collision with
    // genuinely protected-ref cache volumes for the same destination.
    assert.True(t, strings.HasSuffix(strings.SplitN(binds[0], ":", 2)[0], "-protected"))
}
```
Expected assertion: the bound volume name/host path for an unprotected-ref build matches byte-for-byte what a protected-ref build would produce for the same cache destination, demonstrating cross-privilege cache aliasing.

### Citations

**File:** executors/docker/volume.go (L14-16)
```go
	protectedKeyIdx := slices.IndexFunc(e.Build.Cache, func(c spec.Cache) bool {
		return strings.HasSuffix(c.Key, "-protected") && !strings.HasSuffix(c.Key, "-non_protected")
	})
```

**File:** executors/docker/volume.go (L26-28)
```go
		// the volume should be protected if the ref is protected OR if any of the cache volumes have the protected
		// suffix. See https://gitlab.com/gitlab-org/gitlab/-/work_items/494478.
		Protected: e.Build.IsProtected() || protectedKeyIdx >= 0,
```
