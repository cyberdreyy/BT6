### Title
`resumeDependencies` adopts an attacker-supplied container ID with no ownership/identity check - ([File: executors/docker/docker.go])

### Summary
`(*executor).resumeDependencies` (executors/docker/docker.go:1515-1577) trusts the `build-container-id` extracted by `parseEnvKeyFields` (executors/docker/environment_key_fields.go:32-51) and calls `e.dockerConn.ContainerInspect` on it with no check that the container actually belongs to this job, this project, or even this runner. The only gate that ties an `EnvironmentKey` back to the issuing runner (`RunnerID`/`SystemID` comparison) lives in the autoscaler wrapper (`executors/internal/autoscaler/executor.go:190-209`), not in the docker executor itself.

### Finding Description
`common.ParseEnvironmentKey` (common/environment_key.go:26-58) returns an `EnvironmentKey{RunnerID, SystemID, Fields}`. In `resumeDependencies`:
```
envKey, err := common.ParseEnvironmentKey(e.Build.EnvironmentKey())
...
fields, err := parseEnvKeyFields(envKey.Fields)
...
buildInspect, err := e.dockerConn.ContainerInspect(e.Context, fields.buildContainerID)
```
`envKey.RunnerID` and `envKey.SystemID` are parsed but never read afterward - `resumeDependencies` only consumes `envKey.Fields`, and `parseEnvKeyFields` itself does nothing but presence-check the `build-container-id`/`helper-id` strings (executors/docker/environment_key_fields.go:32-51). There is no comparison against `e.Config`/`e.Build.Runner` identity, no container label check (project ID, job ID, runner token, or runner-generated container name prefix), and no ownership marker verified before `e.buildContainer = &buildInspect` is set and the rest of the job (script execution via docker exec) proceeds against that container.

The `RunnerID`/`SystemID` check that does exist is implemented one layer up, only in the autoscaler executor's `validateEnvKey` (executors/internal/autoscaler/executor.go:190-209), which is exercised solely when the autoscaler wraps the docker executor for fleeting/taskscaler-managed VMs. That check also only confirms "same runner config, same machine" - it says nothing about job or project identity, so on a runner host that concurrently executes jobs from multiple projects (a normal, supported runner configuration, whether via autoscaler or a directly-configured docker executor with concurrent job slots), any interruptible/suspendable job on that runner can supply a `build-container-id` belonging to a different job/project's still-running container and have it silently adopted.

Existing protections that were checked and found insufficient:
- `parseEnvKeyFields` only validates required-field presence, not ownership (executors/docker/environment_key_fields.go:32-51).
- `resumeDependencies` only checks that `ContainerInspect` succeeds (i.e., the container exists), returning success on any container ID docker can find - see `TestResumeDependencies` (executors/docker/docker_test.go:3699-3758), which shows the function happily adopts whatever container ID is supplied as long as it inspects successfully; there is no assertion anywhere in the test suite that ties the adopted container to the requesting job/project.
- The `RunnerID`/`SystemID` check exists only in the autoscaler wrapper, not in the docker executor, and even there does not bind to job/project.

### Impact Explanation
An attacker's job adopts another job's running build container as its own `e.buildContainer`/`e.buildContainerID`, after which the runner will execute the attacker's script stages (`docker exec`) inside that foreign container. This grants the attacker command execution inside a container created for a different job/project, exposing that job's filesystem, in-container secrets/environment variables, mounted volumes, and network access - a concrete cross-project container takeover and job impersonation, consistent with the invariant "a normal job must not... access another project's workload."

### Likelihood Explanation
Requires: (1) `SuspendableEnvironments` feature flag enabled, (2) the attacker's own job set to suspend/interrupt and resume (`SuspendOnSuccess`/`SuspendOnFailure`) so the resume code path (`resumeDependencies`) is invoked with an `EnvironmentKey` the attacker can influence, and (3) knowledge or guessability of a live container ID belonging to a concurrently running job on the same shared runner host. Container IDs are 64-hex-character Docker-generated identifiers, not secret but also not guessable at random - however, they may be discoverable via other side channels (e.g., leaked in shared logs, `docker ps` if the attacker has any command execution on the shared host, or via a previous job on the same host). This is a plausible but not trivial precondition; the core bug is the complete absence of an ownership check in `resumeDependencies`, independent of how the container ID is obtained.

### Recommendation
In `resumeDependencies`, before adopting `buildInspect`, verify the target container carries labels proving it was created for this exact job/project/runner (e.g., compare against the labels set by `createLabeler`/`labels` package - runner token hash, project ID, job ID) and reject the resume if they don't match. Additionally, propagate and enforce the `EnvironmentKey.RunnerID`/`SystemID` check inside the docker executor itself (not only in the autoscaler wrapper) so plain (non-autoscaler) docker executors get the same protection, and extend that check to include job/project scoping, not just runner/machine scoping.

### Proof of Concept
```go
func TestResumeDependencies_RejectsForeignContainer(t *testing.T) {
    c := docker.NewMockClient(t)
    e := executorWithMockClient(c)
    require.NoError(t, e.dockerConnector.Connect(t.Context(), common.ExecutorPrepareOptions{}, e))
    e.Config = common.RunnerConfig{RunnerCredentials: common.RunnerCredentials{Token: "attacker-runner-token"}}
    e.Config.Docker = &common.DockerConfig{}
    e.Build = &common.Build{Runner: &common.RunnerConfig{}}
    e.Build.Variables = append(e.Build.Variables,
        spec.Variable{Key: featureflags.SuspendableEnvironments, Value: "true"},
    )
    // Attacker-crafted key pointing at a container that belongs to a different
    // project's job, e.g. carrying labels project-id=OTHER, job-id=OTHER.
    e.Build.Job.SuspendOptions.EnvironmentKey =
        "1/system-id/build-container-id=victim-cid&helper-id=helper-cid"

    victimInspect := container.InspectResponse{
        ID: "victim-cid",
        Config: &container.Config{
            Labels: map[string]string{
                "com.gitlab.gitlab-runner.project.id": "victim-project",
                "com.gitlab.gitlab-runner.job.id":      "victim-job",
            },
        },
        HostConfig: &container.HostConfig{},
    }
    c.On("ContainerInspect", mock.Anything, "victim-cid").Return(victimInspect, nil).Once()

    err := e.resumeDependencies()
    // Expected (fixed) behavior: reject because labels don't match this job/project.
    require.Error(t, err)
    assert.Contains(t, err.Error(), "container does not belong to this job")

    // Current behavior: err is nil and e.buildContainer == &victimInspect,
    // demonstrating the takeover.
}
```
Expected assertions after a fix: `resumeDependencies` returns an error and does not set `e.buildContainer`/`e.buildContainerID` when the inspected container's ownership labels (project/job/runner identity) do not match the resuming build. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

### Citations

**File:** executors/docker/docker.go (L1515-1537)
```go
func (e *executor) resumeDependencies() error {
	envKey, err := common.ParseEnvironmentKey(e.Build.EnvironmentKey())
	if err != nil {
		return err
	}
	fields, err := parseEnvKeyFields(envKey.Fields)
	if err != nil {
		return err
	}

	buildInspect, err := e.dockerConn.ContainerInspect(e.Context, fields.buildContainerID)
	if err != nil {
		return fmt.Errorf("build container %s not found: %w", fields.buildContainerID, err)
	}
	e.buildContainerID = buildInspect.ID
	e.buildContainer = &buildInspect

	helperInspect, err := e.dockerConn.ContainerInspect(e.Context, fields.helperContainerID)
	if err != nil {
		return fmt.Errorf("helper container %s not found: %w", fields.helperContainerID, err)
	}
	e.helperContainer = &helperInspect

```

**File:** executors/docker/environment_key_fields.go (L32-51)
```go
func parseEnvKeyFields(fields url.Values) (envKeyFields, error) {
	k := envKeyFields{
		buildContainerID:  fields.Get(envKeyBuildContainerIDField),
		helperContainerID: fields.Get(envKeyHelperIDField),
	}
	if k.buildContainerID == "" {
		return envKeyFields{}, fmt.Errorf("%s is required", envKeyBuildContainerIDField)
	}
	if k.helperContainerID == "" {
		return envKeyFields{}, fmt.Errorf("%s is required", envKeyHelperIDField)
	}
	if raw := fields.Get(envKeyServiceIDsField); raw != "" {
		k.serviceContainerIDs = strings.Split(raw, ",")
		for _, id := range k.serviceContainerIDs {
			if id == "" {
				return envKeyFields{}, fmt.Errorf("%s contains an empty ID", envKeyServiceIDsField)
			}
		}
	}
	return k, nil
```

**File:** common/environment_key.go (L26-58)
```go
func ParseEnvironmentKey(s string) (EnvironmentKey, error) {
	parts := strings.SplitN(s, "/", 3)
	if len(parts) != 3 {
		return EnvironmentKey{}, fmt.Errorf("environment key: expected at least two '/' separators")
	}

	runnerID, err := strconv.ParseInt(parts[0], 10, 64)
	if err != nil {
		return EnvironmentKey{}, fmt.Errorf("environment key: invalid runner ID: %w", err)
	}
	if runnerID <= 0 {
		return EnvironmentKey{}, fmt.Errorf("environment key: runner ID must be positive")
	}

	systemID, err := url.PathUnescape(parts[1])
	if err != nil {
		return EnvironmentKey{}, fmt.Errorf("environment key: invalid system ID encoding: %w", err)
	}
	if systemID == "" {
		return EnvironmentKey{}, fmt.Errorf("environment key: system ID is empty")
	}

	fields, err := url.ParseQuery(parts[2])
	if err != nil {
		return EnvironmentKey{}, fmt.Errorf("environment key: invalid fields: %w", err)
	}

	return EnvironmentKey{
		RunnerID: runnerID,
		SystemID: systemID,
		Fields:   fields,
	}, nil
}
```

**File:** executors/internal/autoscaler/executor.go (L190-209)
```go
func validateEnvKey(envKey string, runnerID int64, systemID string) (string, url.Values, error) {
	key, err := common.ParseEnvironmentKey(envKey)
	if err != nil {
		return "", nil, err
	}

	if key.RunnerID != runnerID {
		return "", nil, errors.New("environment key was not issued by this runner")
	}
	if key.SystemID != systemID {
		return "", nil, errors.New("environment key was not issued by this machine")
	}

	data, executorFields, err := parseEnvKeyFields(key.Fields)
	if err != nil {
		return "", nil, fmt.Errorf("environment key fields: %w", err)
	}

	return data.acqKey, executorFields, nil
}
```

**File:** executors/docker/docker_test.go (L3699-3758)
```go
func TestResumeDependencies(t *testing.T) {
	c := docker.NewMockClient(t)
	e := executorWithMockClient(c)
	require.NoError(t, e.dockerConnector.Connect(t.Context(), common.ExecutorPrepareOptions{}, e))
	e.Config = common.RunnerConfig{}
	e.Config.Docker = &common.DockerConfig{WaitForServicesTimeout: -1}
	e.Build = &common.Build{
		Runner: &common.RunnerConfig{
			RunnerCredentials: common.RunnerCredentials{Token: "test-token"},
		},
	}
	e.Build.Variables = append(e.Build.Variables,
		spec.Variable{Key: featureflags.SuspendableEnvironments, Value: "true"},
	)
	e.Build.Job.SuspendOptions.EnvironmentKey =
		"1/system-id/build-container-id=build-cid&helper-id=helper-cid&service-ids=svc-a"

	buildInspect := container.InspectResponse{
		ID:         "build-cid",
		HostConfig: &container.HostConfig{NetworkMode: container.NetworkMode(network.NetworkDefault)},
		Mounts: []container.MountPoint{
			{Type: mount.TypeVolume, Name: "vol-x", Source: "/var/lib/docker/volumes/vol-x/_data", Destination: "/builds"},
		},
	}
	svcInspect := container.InspectResponse{
		ID:    "svc-a",
		Name:  "/svc-a",
		State: &container.State{Status: "running"},
		NetworkSettings: &container.NetworkSettings{
			Networks: map[string]*network.EndpointSettings{
				"bridge": {IPAddress: netip.MustParseAddr("172.17.0.4")},
			},
		},
		Config: &container.Config{
			ExposedPorts: network.PortSet{network.MustParsePort("80/tcp"): {}},
		},
	}

	c.On("ContainerInspect", mock.Anything, "build-cid").Return(buildInspect, nil).Once()
	c.On("ContainerInspect", mock.Anything, "helper-cid").Return(container.InspectResponse{
		ID: "helper-cid",
	}, nil).Once()
	c.On("VolumeInspect", mock.Anything, "vol-x").Return(volume.Volume{Name: "vol-x"}, nil).Once()
	c.On("ContainerInspect", mock.Anything, "svc-a").Return(svcInspect, nil)
	c.On("ContainerStart", mock.Anything, "svc-a", client.ContainerStartOptions{}).Return(nil).Once()

	require.NoError(t, e.resumeDependencies())
	assert.Equal(t, "build-cid", e.buildContainerID)
	require.NotNil(t, e.buildContainer)
	assert.Equal(t, "build-cid", e.buildContainer.ID)
	require.NotNil(t, e.helperContainer)
	assert.Equal(t, "helper-cid", e.helperContainer.ID)
	assert.Contains(t, e.temporary, "build-cid")
	assert.Contains(t, e.temporary, "svc-a")
	assert.Contains(t, e.temporary, "helper-cid")
	assert.Len(t, e.services, 1)
	assert.Equal(t, "svc-a", e.services[0].ID)
	assert.Equal(t, []string{"172.17.0.4"}, e.services[0].IP)
	assert.Equal(t, []int{80}, e.services[0].Ports)
}
```
