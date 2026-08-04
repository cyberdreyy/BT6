### Title
Job-configured volume mount can collide with and shadow the `scripts` emptyDir path used for the trusted helper binary - (File: executors/kubernetes/steps_pod.go)

### Summary
`stepsVolumeMounts` builds the container `VolumeMounts` list by always putting the `scripts` mount (at `s.scriptsDir()`) first and then blindly appending whatever `s.getVolumeMountsForConfig()` returns, with no check that a job-configured mount's `MountPath` does not collide with `scriptsDir()`. Because Kubernetes/container runtimes apply mounts in list order (a later mount to the same path takes effect), a job-supplied volume mounted at `scriptsDir()` will shadow the volume that the bootstrap init container wrote `gitlab-runner-helper` into, undermining the trust boundary that `stepsRunnerBinaryPath()` relies on.

### Finding Description
`stepsVolumeMounts()` constructs the mount list as: [1](#0-0) 
The first entry is always the `scripts` emptyDir at `s.scriptsDir()`; the job/config-derived mounts from `getVolumeMountsForConfig()` are appended immediately after with no path-collision check. This same `stepsVolumeMounts()` list is used both for the bootstrap init container (`buildStepsBootstrapInitContainer`, which writes the helper binary to `s.stepsRunnerBinaryPath()`) and for the build container (`stepsBuildContainer`, whose overridden `Command` executes `s.stepsRunnerBinaryPath()` + "steps serve"): [2](#0-1) [3](#0-2) 

Notably, the codebase does implement a collision guard for a different reserved path — the cache directory — showing the pattern is known and intentionally applied elsewhere but omitted for `scripts`: [4](#0-3) 

No equivalent guard exists for `scriptsDir()` in `stepsVolumeMounts` or `stepsVolumes`. If the runner configuration permits volume overwrites from job/pipeline variables and a job supplies a volume whose mount path equals `s.scriptsDir()`, that volume mount is appended after the `scripts` mount in the same container's `VolumeMounts` slice. Kubernetes' pod admission does not reject duplicate `MountPath` entries within a container, and container runtimes apply mounts in order, so the later (job-controlled) mount takes effect at that path in both the init container and the build container.

### Impact Explanation
If the job-controlled volume wins at `scriptsDir()`, the init container's write of `gitlab-runner-helper` either lands in a job-controlled volume (if the collision is present at init-container time too, since it uses the same `stepsVolumeMounts()`) or the build container mounts a different backing store than the one the init container wrote into. In either case, `stepsRunnerBinaryPath()` executed by the build container's `Command` (`<path> steps serve`) can end up resolving to attacker-influenced content/backing storage rather than the trusted helper binary the init container placed, undermining the invariant that the shared `scripts` volume carrying the trusted helper must not be overridable by job-supplied volume config.

### Likelihood Explanation
Exploitability depends entirely on the precondition that the runner configuration allows job/pipeline variables to influence the mount paths returned by `getVolumeMountsForConfig()`. Given that precondition, the exploit requires no special privilege beyond normal job/pipeline authoring — just declaring a volume whose `MountPath` matches `scriptsDir()`. The lack of any collision check specific to `scripts` (contrasted with the explicit check present for `cache`) makes this a straightforward, repeatable configuration rather than a probabilistic race.

### Recommendation
Add an explicit collision check in `stepsVolumeMounts()`/`stepsVolumes()` (mirroring `isDefaultCacheDirVolumeRequired`) that rejects or drops any job/config-supplied volume mount whose `MountPath` equals `s.scriptsDir()` (and ideally `s.AbstractExecutor.RootDir()`/`CacheDir()` for defense-in-depth), returning a hard error from `stepsBuildContainer`/`buildStepsBootstrapInitContainer` construction rather than silently allowing the shadow.

### Proof of Concept
Go unit test outline for `steps_pod_test.go`:
```go
func TestStepsVolumeMounts_RejectsScriptsDirCollision(t *testing.T) {
    s := newTestStepsExecutor(t) // existing test helper for *executor
    s.Config.Kubernetes.Volumes.HostPath = []common.KubernetesHostPath{
        {Name: "attacker", MountPath: s.scriptsDir(), HostPath: "/tmp/attacker"},
    }

    mounts := s.stepsVolumeMounts()

    // Expected (fixed) behavior: either an error is returned by the
    // container-building functions, or "scripts" is guaranteed to be the
    // last/only mount at scriptsDir().
    scriptsCount := 0
    lastScriptsIsTrusted := true
    for i, m := range mounts {
        if m.MountPath == s.scriptsDir() {
            scriptsCount++
            lastScriptsIsTrusted = m.Name == "scripts" && i == len(mounts)-1 || scriptsCount == 1
        }
    }
    assert.Equal(t, 1, scriptsCount, "no other volume should be allowed to mount at scriptsDir()")
}
```
Current behavior: `scriptsCount` will be 2 (the built-in `scripts` mount plus the attacker-controlled mount at the same path), demonstrating the missing guard.

### Citations

**File:** executors/kubernetes/steps_pod.go (L121-139)
```go
func (s *executor) buildStepsBootstrapInitContainer() (api.Container, error) {
	pullPolicy, err := s.pullManager.GetPullPolicyFor(stepsBootstrapInitContainerName)
	if err != nil {
		return api.Container{}, fmt.Errorf("getting pull policy for steps bootstrap: %w", err)
	}

	return api.Container{
		Name:            stepsBootstrapInitContainerName,
		Image:           s.getHelperImage(),
		ImagePullPolicy: pullPolicy,
		// Image is the helper image, which has gitlab-runner-helper on
		// $PATH, so the bare basename suffices here. The build container
		// (which runs the user's image) cannot make the same assumption
		// and must use the absolute path of the bootstrapped binary in
		// the scripts emptyDir, hence the s.stepsRunnerBinaryPath() arg.
		Command: []string{
			helperBinaryName, "steps", "bootstrap", s.stepsRunnerBinaryPath(),
		},
		VolumeMounts: s.stepsVolumeMounts(),
```

**File:** executors/kubernetes/steps_pod.go (L311-350)
```go
	// The user image's own Entrypoint/Cmd are intentionally overridden:
	// the build container's purpose in Concrete mode is to host the
	// step-runner serve process, not to run the image's default command.
	command := []string{s.stepsRunnerBinaryPath(), "steps", "serve"}
	command = append(command, s.BuildShell.DockerCommand...)

	containerPorts := make([]api.ContainerPort, len(s.options.Image.Ports))
	proxyPorts := make([]proxy.Port, len(s.options.Image.Ports))
	for i, port := range s.options.Image.Ports {
		proxyPorts[i] = proxy.Port{Name: port.Name, Number: port.Number, Protocol: port.Protocol}
		containerPorts[i] = api.ContainerPort{ContainerPort: int32(port.Number)}
	}

	// Register session proxies for the build container's declared ports,
	// mirroring the standard buildContainer path so attach/legacy port
	// proxying is preserved in Concrete mode. setupStepsPod consumes the
	// ProxyPool via makePodProxyServices. The build container's name is
	// always buildContainerName, so the alias fallback is that name.
	if len(proxyPorts) > 0 {
		aliases := s.options.Image.Aliases()
		if len(aliases) == 0 {
			aliases = []string{buildContainerName}
		}
		for _, serviceName := range aliases {
			s.ProxyPool[serviceName] = s.newProxy(serviceName, proxyPorts)
		}
	}

	return api.Container{
		Name:            buildContainerName,
		Image:           s.options.Image.Name,
		ImagePullPolicy: pullPolicy,
		Command:         command,
		Env:             nil,
		Resources: api.ResourceRequirements{
			Limits:   s.configurationOverwrites.buildLimits,
			Requests: s.configurationOverwrites.buildRequests,
		},
		Ports:           containerPorts,
		VolumeMounts:    s.stepsVolumeMounts(),
```

**File:** executors/kubernetes/steps_pod.go (L532-540)
```go
func (s *executor) stepsVolumeMounts() []api.VolumeMount {
	mounts := []api.VolumeMount{
		{
			Name:      "scripts",
			MountPath: s.scriptsDir(),
		},
	}

	mounts = append(mounts, s.getVolumeMountsForConfig()...)
```

**File:** executors/kubernetes/steps_pod.go (L566-575)
```go
func (s *executor) isDefaultCacheDirVolumeRequired() bool {
	cacheDir := s.AbstractExecutor.CacheDir()
	for _, mount := range s.getVolumeMountsForConfig() {
		if mount.MountPath == cacheDir {
			return false
		}
	}

	return true
}
```
