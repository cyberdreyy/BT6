### Title
Missing reservation of internal network aliases (`build`, container names) lets a job-supplied service alias collide with the main build container's DNS alias - (executors/docker/services.go, executors/docker/docker.go)

### Summary
`createFromServiceDefinition`/`SplitNameAndVersion` build a service's network aliases purely from user-controlled `.gitlab-ci.yml` `services[].alias` values, and the only de-duplication performed is against aliases used by *other job-defined services* (the `linksMap` check). Nothing prevents a job author from choosing the alias `build`, which is the same literal alias GitLab Runner hard-codes for the main build/helper container's network endpoint in `createContainer`.

### Finding Description
- `SplitNameAndVersion` (helpers/container/services/services.go:27-76) derives the `__`/`-` variant aliases automatically, but the actual "attacker surface" is `Service.Alias`/`spec.Image.Aliases()`, which is copied verbatim into `serviceMeta.Aliases` in `createFromServiceDefinition` (executors/docker/services.go:161-171) with no filtering of reserved words.
- The only collision check is `if linksMap[linkName] != nil { ...Skipping... }` (executors/docker/services.go:173-180), which only rejects an alias already claimed by another **service** created in the same job — it never consults the aliases already reserved for the build/helper container.
- Separately, `createContainer` (executors/docker/docker.go:929-964) always calls `cfgTor.NetworkConfig([]string{"build", containerName})` (line 961) for build/predefined containers, statically assigning `"build"` as a network alias on the per-build user-defined network (only relevant when `e.networkMode.IsUserDefined()`, i.e. `FF_NETWORK_PER_BUILD=true` or `network_mode` set), regardless of `containerType`.
- Because the reserved alias `"build"` is applied to the *build container's own endpoint config* (`e.networkConfig` in docker.go:749-780) and a malicious service's alias `"build"` is applied to *the service's own endpoint config* via the same `e.networkConfig` call (docker.go:507, `createService`), both containers end up registering the identical alias with Docker's embedded DNS on the same user-defined network. Docker's embedded DNS permits multiple containers to share a network alias (it does not error on `ContainerCreate`), so the call succeeds silently — `ContainerCreate` at docker.go:513 has no server-side or runner-side rejection of the duplicate alias.

### Impact Explanation
An unprivileged pipeline author who sets `services: [{name: evil-image, alias: build}]` can cause the shared per-build Docker network to have two containers registered under the alias `build`: the legitimate build container and the attacker's service container. Any DNS lookup for `build` within that network becomes non-deterministic between the two endpoints (Docker returns multiple A records / round-robins rather than deterministically preferring the real build container), so any consumer inside the network that resolves `build` can be misdirected to the attacker's container. In practice, no component of GitLab Runner itself (trace upload, artifact/cache transfer, session server) relies on resolving the `build` alias over the container network — the Runner talks to containers through the Docker Engine API (`ContainerAttach`/`ContainerExec`), not via network DNS — so the exploitation surface is limited to job-defined services or the job script itself intentionally or unintentionally resolving `build`. This reduces, but does not eliminate, the concrete "session/job hijack" impact claimed in the question; the more accurate scoped impact is best described as a network-alias collision / traffic-misdirection primitive within the per-build network, not a demonstrated secret or session leak.

### Likelihood Explanation
Preconditions are attacker-reachable and cheap: `FF_NETWORK_PER_BUILD=true` (or a configured `network_mode`) and a `.gitlab-ci.yml` `services` entry with `alias: build`. No special privilege beyond normal pipeline authoring is required, and the code path (`createFromServiceDefinition` → `createService` → `e.networkConfig` → `ContainerCreate`) is exercised on every job using services. It is fully repeatable.

### Recommendation
- In `createFromServiceDefinition` (executors/docker/services.go), reject or rename any user-supplied service alias that matches reserved names (`"build"`, the computed `containerName`, and any helper/predefined container alias), instead of only checking against `linksMap`.
- Alternatively, stop hard-coding `"build"` as a network alias in `createContainer` (executors/docker/docker.go:961) for all container types, or make it explicitly non-overridable by validating service aliases against the reserved set before calling `e.networkConfig`.

### Proof of Concept
```go
func TestCreateFromServiceDefinition_RejectsReservedAlias(t *testing.T) {
    e := newTestExecutor(t) // set FF_NETWORK_PER_BUILD / userDefined network mode
    linksMap := map[string]*serviceInfo{}

    err := e.createFromServiceDefinition(0, spec.Image{
        Name:  "attacker/evil:latest",
        Alias: "build", // collides with reserved build-container alias
    }, linksMap)

    // Expected (currently failing): alias must be rejected or not applied
    require.NoError(t, err)
    _, ok := linksMap["build"]
    assert.False(t, ok, "service must not be allowed to claim the reserved 'build' network alias")
}
```
Combine with an integration test asserting that after `createContainer(buildContainerType, ...)` and `createService(...)` with alias `build`, the Docker network inspect (`NetworkInspect`) shows only the real build container's endpoint holding alias `build`. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

### Citations

**File:** helpers/container/services/services.go (L65-76)
```go
	alias := strings.ReplaceAll(service.Service, "/", "__")
	service.Aliases = append(service.Aliases, alias)

	// Create alternative link name according to RFC 1123
	// Where you can use only `a-zA-Z0-9-`
	alternativeName := strings.ReplaceAll(service.Service, "/", "-")
	if alias != alternativeName {
		service.Aliases = append(service.Aliases, alternativeName)
	}

	return service
}
```

**File:** executors/docker/services.go (L161-205)
```go
func (e *executor) createFromServiceDefinition(
	serviceIndex int,
	serviceDefinition spec.Image,
	linksMap map[string]*serviceInfo,
) error {
	var container *serviceInfo

	serviceMeta := services.SplitNameAndVersion(serviceDefinition.Name)
	if len(serviceDefinition.Aliases()) != 0 {
		serviceMeta.Aliases = append(serviceMeta.Aliases, serviceDefinition.Aliases()...)
	}

	for _, linkName := range serviceMeta.Aliases {
		if linksMap[linkName] != nil {
			e.BuildLogger.Warningln(fmt.Sprintf(
				"Skipping alias %q for service %q (services[%d]): alias is already in use by another service.",
				linkName, serviceDefinition.Name, serviceIndex,
			))
			continue
		}

		// Create service if not yet created
		if container == nil {
			var err error
			container, err = e.createService(
				serviceIndex,
				serviceMeta.Service,
				serviceMeta.Version,
				serviceMeta.ImageName,
				serviceDefinition,
				serviceMeta.Aliases,
			)
			if err != nil {
				return err
			}

			e.BuildLogger.Debugln("Created service", serviceDefinition.Name, "as", container.ID)
			e.services = append(e.services, container)
			e.temporary = append(e.temporary, container.ID)

			// add 12-character container ID as hostname
			linksMap[container.ID[:min(12, len(container.ID))]] = container
		}
		linksMap[linkName] = container
	}
```

**File:** executors/docker/docker.go (L454-513)
```go
func (e *executor) createService(
	serviceIndex int,
	service, version, image string,
	definition spec.Image,
	linkNames []string,
) (*serviceInfo, error) {
	if service == "" {
		return nil, &common.BuildError{Inner: fmt.Errorf("invalid service image name: %s", definition.Name), FailureReason: common.ConfigurationError}
	}

	if e.volumesManager == nil {
		return nil, errVolumesManagerUndefined
	}

	var serviceName string
	if strings.HasPrefix(version, "@sha256") {
		serviceName = fmt.Sprintf("%s%s...", service, version) // service@digest
	} else {
		serviceName = fmt.Sprintf("%s:%s...", service, version) // service:version
	}

	dockerOptions := definition.ExecutorOptions.Docker.Expand(e.Build.GetAllVariables())

	e.BuildLogger.Println("Starting service", serviceName)
	serviceImage, err := e.pullManager.GetDockerImage(image, dockerOptions, definition.PullPolicies)
	if err != nil {
		return nil, err
	}

	serviceSlug := strings.ReplaceAll(service, "/", "__")
	containerName := e.makeContainerName(fmt.Sprintf("%s-%d", serviceSlug, serviceIndex))

	// this will fail potentially some builds if there's name collision
	_ = e.removeContainer(e.Context, containerName)

	config := e.createServiceContainerConfig(service, version, imageReferenceForCreate(serviceImage), definition)

	devices, err := e.getServicesDevices(image)
	if err != nil {
		return nil, err
	}

	deviceRequests, err := e.getServicesDeviceRequests()
	if err != nil {
		return nil, err
	}

	hostConfig, err := e.createHostConfigForService(e.isInPrivilegedServiceList(definition), devices, deviceRequests)
	if err != nil {
		return nil, err
	}

	platform := platformForImage(serviceImage, definition.ExecutorOptions)
	networkConfig, err := e.networkConfig(linkNames)
	if err != nil {
		return nil, err
	}

	e.BuildLogger.Debugln("Creating service container", containerName, "...")
	resp, err := e.dockerConn.ContainerCreate(e.Context, config, hostConfig, networkConfig, platform, containerName)
```

**File:** executors/docker/docker.go (L749-780)
```go
func (e *executor) networkConfig(aliases []string) (*network.NetworkingConfig, error) {
	// setting a container's mac-address changed in API version 1.44
	if e.serverAPIVersion.LessThan(version1_44) {
		return e.networkConfigLegacy(aliases), nil
	}

	mac, err := parseMACAddress(e.Config.Docker.MacAddress)
	if err != nil {
		return nil, &common.BuildError{Inner: err, FailureReason: common.ConfigurationError}
	}

	nm := string(e.networkMode)
	nc := network.NetworkingConfig{}

	if nm == "" {
		// docker defaults to using "bridge" network driver if none was specified.
		nc.EndpointsConfig = map[string]*network.EndpointSettings{
			network.NetworkDefault: {MacAddress: mac},
		}
		return &nc, nil
	}

	nc.EndpointsConfig = map[string]*network.EndpointSettings{
		nm: {MacAddress: mac},
	}

	if e.networkMode.IsUserDefined() {
		nc.EndpointsConfig[nm].Aliases = aliases
	}

	return &nc, nil
}
```

**File:** executors/docker/docker.go (L929-964)
```go
func (e *executor) createContainer(
	containerType string,
	imageDefinition spec.Image,
	allowedInternalImages []string,
	cfgTor containerConfigurator,
) (*container.InspectResponse, error) {
	if e.volumesManager == nil {
		return nil, errVolumesManagerUndefined
	}

	image, err := e.expandAndGetDockerImage(
		imageDefinition.Name,
		allowedInternalImages,
		imageDefinition.ExecutorOptions.Docker,
		imageDefinition.PullPolicies,
	)
	if err != nil {
		return nil, err
	}

	containerName := e.makeContainerName(containerType)

	config, err := cfgTor.ContainerConfig(image)
	if err != nil {
		return nil, fmt.Errorf("failed to create container configuration: %w", err)
	}

	hostConfig, err := cfgTor.HostConfig()
	if err != nil {
		return nil, err
	}

	networkConfig, err := cfgTor.NetworkConfig([]string{"build", containerName})
	if err != nil {
		return nil, err
	}
```
