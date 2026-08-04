### Title
Image-by-ID short-circuit allows credential-less access to another tenant's cached image on shared Docker hosts - (File: executors/docker/internal/pull/manager.go)

### Summary
`getImageUsingPullPolicy` skips pulling and returns whatever `docker image inspect <imageName>` resolves to whenever `existingImage.ID == imageName`, with no ownership or provenance check tying that cached image to the current job's credentials. On a shared runner host where the local Docker image cache is reused across projects, a job can supply an image reference (e.g. a `sha256:<digest>` ID) that Docker resolves to an image previously pulled by a different, unrelated project, and receive a container built from that image without presenting any registry credentials for it.

### Finding Description
`GetDockerImage` -> `getImageUsingPullPolicy` calls `m.client.ImageInspectWithRaw(m.context, imageName, platform)` [1](#0-0) . Docker's inspect API resolves `imageName` against any local reference form: full ID, truncated ID, digest, or tag — it is host-local image cache lookup, not scoped to any tenant/project. If the lookup succeeds and `existingImage.ID == imageName` (i.e., the job supplied the image's own ID/digest as the `image:` field), the code returns immediately: `"Don't pull image that is passed by ID"` [2](#0-1) . This branch is reached before `resolveAuthConfigForImage`/`pullDockerImage` and before `VerifyAllowedImage`-style checks are even consulted at this layer — no auth is resolved, and the returned image comes straight from local cache.

The `allowed_images` config (`common/allowed_images.go`, `VerifyAllowedImage`) is a separate mechanism enforced by the executor layer against the raw string the job specified; it does not verify that the *resolved local image ID* corresponds to something the job's own credentials are entitled to pull, and by default (`AllowedImages` empty) it allows any image string, including digests/IDs. So on a runner configured without a restrictive `allowed_images` allowlist — the common/default case — a job specifying `sha256:<hash>` (or a resolvable short ID) as the image passes straight through to the by-ID short-circuit.

Preconditions:
- The runner host's Docker image cache is shared across jobs/projects (true for any Docker executor with `pull_policy` reuse across concurrent/sequential jobs from different projects, not just an "admin misconfiguration" — it is the normal, documented behavior of local image caching, distinct from things like privileged mode or docker.sock exposure).
- A prior job (from any project on that host) pulled a private image, leaving it in the local Docker image store with a known/derivable ID or digest.
- The attacker knows or guesses that content digest. Content digests are frequently visible in job logs, `docker inspect` output shown by prior jobs, artifacts, or simply by an attacker's own earlier job on the same image on a public registry (digest is a content hash, not a secret, but here it's being used as a stand-in key granting access to *whatever local image the ID currently maps to*).

This is a genuine logic gap: the "pull by ID" short-circuit was designed to avoid re-pulling an image the *same* job/pipeline already resolved (paired with `wasImageUsed`), but it also fires for jobs that never pulled that image themselves and hold no credentials for it, as long as the ID happens to already exist in the shared local cache.

### Impact Explanation
An unprivileged job on a shared Docker-executor host can obtain a fully materialized container from another project's privately-pulled image (proprietary base image, internal tooling image, etc.) without supplying valid registry credentials, by referencing it via its digest/ID. This is a cross-project confidentiality violation of pulled image contents (image layers, files, potentially embedded secrets/tooling baked into that private image) — consistent with the "cross-project image-cache read/exfiltration" impact described.

### Likelihood Explanation
Requires a shared-cache multi-tenant runner host (common in shared GitLab Runner fleets using Docker executor with `pull_policy: if-not-present` or similar) and knowledge of the target image's ID/digest. Digests are not inherently secret and often leak through logs/build metadata, so this is feasible for a moderately informed attacker with pipeline access on the shared runner, and is fully repeatable since the local cache persists until GC.

### Recommendation
Remove or gate the by-ID short-circuit so it cannot substitute for `pull_policy: never`'s existing checks across tenants: at minimum, require that the digest/ID short-circuit only apply when the job's own `wasImageUsed` cache (per-manager, per-job/pipeline lifetime) already recorded that ID, or require successful auth resolution against the image's own `RepoDigests`/registry reference before returning a locally cached image by ID, so a job cannot leverage another tenant's cached pull without demonstrating equivalent registry access.

### Proof of Concept
Go unit test in `executors/docker/internal/pull` (extending `manager_test.go`):
1. Configure a mock `docker.Client` (see `helpers/docker/mocks.go`) whose `ImageInspectWithRaw` returns an `image.InspectResponse{ID: "sha256:privateimageid...", RepoDigests: []string{"registry.example.com/private/image@sha256:..."}}` for any call — simulating an image previously pulled by another project.
2. Build a `manager` via `NewManager` with `ManagerConfig{AuthConfig: "", Credentials: nil}` (no credentials for the private registry).
3. Call `GetDockerImage("sha256:privateimageid...", spec.ImageDockerOptions{}, []common.DockerPullPolicy{common.PullPolicyIfNotPresent})`.
4. Assert: `client.ImagePullBlocking` / `imagePullOnce` is never invoked (mock `AssertNotCalled`), and the returned `*image.InspectResponse` equals the private image, proving the manager returned another tenant's cached image without attempting authentication or a pull.

### Citations

**File:** executors/docker/internal/pull/manager.go (L232-248)
```go
func (m *manager) getImageUsingPullPolicy(
	imageName string, options spec.ImageDockerOptions,
	pullPolicy common.DockerPullPolicy,
) (*image.InspectResponse, error) {
	m.logger.Debugln("Looking for image", imageName, "...")

	platform, err := parsePlatform(options.Platform, m.logger)
	if err != nil {
		return nil, &common.BuildError{Inner: err, FailureReason: common.ConfigurationError}
	}

	existingImage, _, err := m.client.ImageInspectWithRaw(m.context, imageName, platform)

	// Return early if we already used that image
	if err == nil && m.wasImageUsed(imageName, existingImage.ID) {
		return &existingImage, nil
	}
```

**File:** executors/docker/internal/pull/manager.go (L255-259)
```go
	if err == nil {
		// Don't pull image that is passed by ID
		if existingImage.ID == imageName {
			return &existingImage, nil
		}
```
