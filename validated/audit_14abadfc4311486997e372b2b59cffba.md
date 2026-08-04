### Title
`dockerLinuxSetter.Set` uses the job-cancellable context for both container wait and cleanup, leaking a `gitlab-runner-cache-init` helper container pinned to a shared reusable cache volume - (File: executors/docker/internal/volumes/permission/linux_set.go)

### Summary
`Set()` passes the same `ctx` argument into both `runContainer` (which calls `ContainerStart`/`waiter.Wait`) and the deferred `ContainerRemove` cleanup, and any `ContainerRemove` error is only logged at Debug level and swallowed rather than surfaced or retried. Because this `ctx` originates from the job's cancellable/timeout-bound build context, a job cancellation during `volumes.manager.createCacheVolume`'s call to `permissionSetter.Set` can cause both the wait and the cleanup remove to fail on the same already-cancelled context, leaving the helper container attached and preventing the shared, reusable cache volume from being cleanly removed or reused.

### Finding Description
`dockerLinuxSetter.Set` [1](#0-0)  creates a permission-setting helper container bound to the target volume via `dstMount` [2](#0-1) , defers a `ContainerRemove(ctx, containerID, ...)` call that logs failures only at `Debug` level [3](#0-2) , then calls `runContainer(ctx, containerID)`, which starts the container and blocks on `d.waiter.Wait(ctx, containerID)` [4](#0-3) .

This is reached from `manager.createCacheVolume`, which calls `VolumeCreate` and then `m.permissionSetter.Set(ctx, v.Name, ...)` for reusable, `UniqueName`-derived cache volumes [5](#0-4) , invoked from `addCacheVolume`/`Create` during job preparation with the job's `ctx` [6](#0-5) .

The job's build context is a timeout/cancel-bound `context.Context` created in `Build.Run`: `ctx, cancel := context.WithTimeout(ctx, b.GetBuildTimeout())`, wired to trace-triggered cancellation via `b.configureTrace(trace, cancel)` [7](#0-6) . This is the same class of context that flows into `ExecutorPrepareOptions.Context` used during `Prepare`, i.e. during volume/permission-container setup. If the job is cancelled at this point, `ctx.Err() != nil` for the remainder of `Set()`'s execution: `waiter.Wait(ctx, containerID)` will fail (as `context.Canceled` typically propagates into Docker API calls), and the deferred `ContainerRemove(ctx, ...)` — using the *same* cancelled `ctx` — will also fail immediately at the client level rather than actually issuing the remove RPC, and that failure is only logged at Debug and not retried or escalated.

Notably, elsewhere in the codebase the project already recognizes this exact class of problem and mitigates it: `createDockerConnection` explicitly documents and fixes a similar issue by deriving a background context with an extended deadline so that cleanup ("Cleanup stage") is not cut short by job cancellation (referencing gitlab-runner issue #38725) [8](#0-7) . `dockerLinuxSetter.Set`'s cleanup path does not apply this same pattern — it reuses the caller-supplied, job-cancellable `ctx` for its own internal cleanup RPC.

### Impact Explanation
If cleanup fails silently, the `gitlab-runner-cache-init` helper container remains attached (bind-mounted) to the reusable cache volume named `UniqueName-cache-hashPath(destination)` [9](#0-8) . Docker generally refuses to remove a volume that is still referenced by an existing container, so subsequent jobs sharing the same cache key/`UniqueName` for that destination could fail to reuse or remove the volume, creating a persistent multi-tenant disruption scoped to that cache path — consistent with the described "Medium persistent multi-tenant disruption" scope.

### Likelihood Explanation
This requires: (1) the runner is configured with Docker executor, cache volumes enabled (not host-based, not disabled), and permission setting active on Linux helper images; (2) the attacker/job author simply cancels or lets the job time out at the narrow window while `permissionSetter.Set` is running (during `Prepare`/volume-creation phase, before job script execution). No special privileges are needed beyond normal job cancellation/timeout, which any project member with pipeline permissions can trigger. The window is narrow but deterministically reachable by anyone able to cancel jobs, and is repeatable across many attempts.

### Recommendation
Detach the cleanup operation in `dockerLinuxSetter.Set` from the caller-supplied `ctx`. Use a background context (optionally with a bounded timeout, e.g. similar to the `dockerCleanupTimeout` pattern already used in `createDockerConnection`) for the deferred `ContainerRemove` call, so cleanup is not subject to job cancellation. Additionally, consider escalating `ContainerRemove` failures beyond a Debug log (e.g., a background reaper or retry) so that orphaned permission-setter containers bound to reusable volumes don't accumulate silently.

### Proof of Concept
Go unit test outline for `executors/docker/internal/volumes/permission`:
```go
func TestSet_CleanupUsesDetachedContext(t *testing.T) {
    fakeClient := &fakeDockerClient{}
    ctx, cancel := context.WithCancel(context.Background())

    fakeClient.onContainerCreate = func(ctx context.Context, ...) (string, error) {
        return "container-id", nil
    }
    fakeClient.onContainerStart = func(ctx context.Context, id string, ...) error {
        cancel() // simulate job cancellation right after start
        return nil
    }
    fakeClient.onWait = func(ctx context.Context, id string) error {
        return ctx.Err() // returns context.Canceled
    }
    fakeClient.onContainerRemove = func(ctx context.Context, id string, ...) error {
        // Assert this ctx is NOT the cancelled caller ctx
        assert.Nil(t, ctx.Err(), "cleanup must not use the cancellable caller ctx")
        return nil
    }

    setter := NewDockerLinuxSetter(fakeClient, logger, helperImage)
    err := setter.Set(ctx, "cache-volume", nil)
    require.Error(t, err) // Wait failed due to cancellation
    // but ContainerRemove must have succeeded with a non-cancelled ctx
}
```
Expected current (buggy) behavior: the `onContainerRemove` assertion fails because `ctx.Err() != nil` — proving cleanup uses the same cancelled context as the caller and can be swallowed as a Debug-level log rather than actually succeeding.

### Citations

**File:** executors/docker/internal/volumes/permission/linux_set.go (L42-68)
```go
func (d *dockerLinuxSetter) Set(ctx context.Context, volumeName string, labels map[string]string) error {
	d.logger = d.logger.WithFields(logrus.Fields{
		"volume_name": volumeName,
		"context":     "set_volume_permission",
	})

	containerID, err := d.createContainer(ctx, volumeName, labels)
	if err != nil {
		return fmt.Errorf("create permission container for volume %q: %w", volumeName, err)
	}

	defer func() {
		removeErr := d.client.ContainerRemove(ctx, containerID, client.ContainerRemoveOptions{Force: true})
		if removeErr != nil {
			d.logger.WithError(removeErr).
				WithField("container_id", containerID).
				Debug("Failed to remove permission set container")
		}
	}()

	err = d.runContainer(ctx, containerID)
	if err != nil {
		return fmt.Errorf("running permission container %q for volume %q: %w", containerID, volumeName, err)
	}

	return nil
}
```

**File:** executors/docker/internal/volumes/permission/linux_set.go (L70-103)
```go
func (d *dockerLinuxSetter) createContainer(
	ctx context.Context,
	volumeName string,
	labels map[string]string,
) (string, error) {
	volumeBinding := fmt.Sprintf("%s:%s", volumeName, dstMount)

	config := &container.Config{
		Image:  d.helperImage.ID,
		Cmd:    []string{"gitlab-runner-helper", "cache-init", dstMount},
		Labels: labels,
	}

	hostConfig := &container.HostConfig{
		LogConfig: container.LogConfig{
			Type: "json-file",
		},
		Binds: []string{volumeBinding},
	}

	uuid, err := helpers.GenerateRandomUUID(8)
	if err != nil {
		return "", fmt.Errorf("generting uuid for permission container: %w", err)
	}

	containerName := fmt.Sprintf("%s-set-permission-%s", volumeName, uuid)
	c, err := d.client.ContainerCreate(ctx, config, hostConfig, nil, nil, containerName)
	if err != nil {
		return "", err
	}
	d.logger.WithField("container_id", c.ID).Debug("Created container to set volume permissions")

	return c.ID, err
}
```

**File:** executors/docker/internal/volumes/permission/linux_set.go (L105-118)
```go
func (d *dockerLinuxSetter) runContainer(ctx context.Context, containerID string) error {
	err := d.client.ContainerStart(ctx, containerID, client.ContainerStartOptions{})
	if err != nil {
		return fmt.Errorf("starting permission container: %w", err)
	}

	err = d.waiter.Wait(ctx, containerID)
	if err != nil {
		return fmt.Errorf("waiting for permission container to finish: %w", err)
	}
	d.logger.WithField("container_id", containerID).Debug("Updated volume permissions")

	return nil
}
```

**File:** executors/docker/internal/volumes/manager.go (L144-167)
```go
func (m *manager) addCacheVolume(ctx context.Context, volume *parser.Volume) error {
	// disable cache for automatic container cache,
	// but leave it for host volumes (they are shared on purpose)
	if m.config.DisableCache {
		m.logger.Debugln(fmt.Sprintf("Cache containers feature is disabled, creating non-reusable volume for %q", volume.Destination))

		volumeName, err := m.createCacheVolume(ctx, volume.Destination, false)
		if err != nil {
			return err
		}

		m.temporaryVolumes = append(m.temporaryVolumes, volumeName)

		return nil
	}

	if m.config.CacheDir != "" {
		return m.createHostBasedCacheVolume(volume.Destination)
	}

	_, err := m.createCacheVolume(ctx, volume.Destination, true)

	return err
}
```

**File:** executors/docker/internal/volumes/manager.go (L194-242)
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
```

**File:** common/build.go (L1560-1563)
```go
	ctx, cancel := context.WithTimeout(ctx, b.GetBuildTimeout())
	defer cancel()

	b.configureTrace(trace, cancel)
```

**File:** executors/docker/docker.go (L242-255)
```go
// createDockerConnection creates a connection to a potentially remote docker daemon. The connection is encapsulated in
// a dockerConnection object which includes a docker.Client instance and, if connecting to a remote docker daemon, an
// executors.Client instance.
//
// Note that in the case of a remote docker daemon, we want to maintain a long-lived connection for the duration of the
// job (including during the Cleanup stage). To achieve this, we don't want the context to be cancelled when the job is
// cancelled or times out, so we create a new context here with a timeout of job-timeout + dockerCleanupTimeout. This
// fixes https://gitlab.com/gitlab-org/gitlab-runner/-/issues/38725.
func createDockerConnection(ctx context.Context, opts common.ExecutorPrepareOptions, e *executor) (*dockerConnection, error) {
	deadline, hasDeadline := ctx.Deadline()
	if !hasDeadline {
		deadline = time.Now().Add(e.Build.GetBuildTimeout())
	}
	ctx, cancel := context.WithDeadline(context.Background(), deadline.Add(dockerCleanupTimeout))
```
