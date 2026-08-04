### Title
Unvalidated user-supplied service alias can collide with the reserved `build` network alias - (File: `helpers/container/services/services.go`, `executors/docker/services.go`)

### Summary
`SplitNameAndVersion` and `createFromServiceDefinition` build the alias list for a service purely from the service image name and the user-supplied `alias`/`aliases` field, with no check against reserved names. Under `FF_NETWORK_PER_BUILD=true`, GitLab Runner documents that it "uses the `build` alias to resolve the job container" on the per-job bridge network, but nothing in the alias-derivation or collision-detection code in `services.go`/`services_test.go` prevents a job author from requesting a service alias literally equal to `build`.

### Finding Description
`SplitNameAndVersion` (helpers/container/services/services.go:27-76) derives `Service.Aliases` from the image name/registry path only (e.g. `namespace__service`, `namespace-service`); it performs no validation or blocklist check on the resulting strings. [1](#0-0) 

In `createFromServiceDefinition` (executors/docker/services.go:161-207), the service's own `serviceDefinition.Aliases()` (i.e. the user-controlled `alias`/`aliases` field from `.gitlab-ci.yml` `services:`) is appended verbatim to `serviceMeta.Aliases`, then each alias is inserted into the shared `linksMap` unless that specific key already exists in `linksMap`: [2](#0-1) 

Critically, `linksMap` is initialized empty in `createServices()` before any service is processed — the string `"build"` is never pre-registered as a reserved/protected key: [3](#0-2) 

The existing collision guard (`if linksMap[linkName] != nil { ... Skipping alias ... }`) only prevents two *services* from claiming the same alias — it does not protect the runner-reserved `build` alias that is documented to resolve to the job container itself under `FF_NETWORK_PER_BUILD`: [4](#0-3) 

Because a pipeline author fully controls the `services:` entries (`name`/`alias`/`aliases`) that flow into `getServicesDefinitions()` → `createFromServiceDefinition()` → `SplitNameAndVersion()`, an attacker can simply set `alias: build` on a service definition. Nothing in `getServicesDefinitions` (executors/docker/services.go:80-104), the allowed-image check (`verifyAllowedImage`), or `createFromServiceDefinition` rejects the reserved word `build`.

### Impact Explanation
If the reserved `build` alias is also the mechanism the Docker daemon uses to resolve the job container's own hostname on the per-build bridge network (as stated in the documentation), then registering the same alias for an attacker-controlled service container introduces ambiguity in that network's alias resolution. Concretely, any traffic (session/exec/terminal proxying or the job's own script logic) that depends on resolving the hostname `build` on the per-job network could be misdirected or non-deterministically resolved to the attacker's service container instead of exclusively to the real build container, which violates the invariant that the build container's own network identity is exclusive to it.

### Likelihood Explanation
Preconditions are attacker-controllable and low-effort: `FF_NETWORK_PER_BUILD=true` (a runner-side setting, but increasingly the recommended/default networking mode per the current docs) plus the `docker` executor, both common production configurations. The attacker action is a single line in `.gitlab-ci.yml`:
```yaml
services:
  - name: some/image
    alias: build
```
No special privilege is needed beyond ordinary pipeline authorship, and the existing alias-collision code only guards against service-vs-service collisions, not against this reserved name, so the path is trivially reachable.

### Recommendation
Add an explicit reserved-alias check in `createFromServiceDefinition` (or earlier, in `getServicesDefinitions`) that rejects or drops any user-supplied alias equal to `build` (case-insensitively, and after any normalization) before it is inserted into `linksMap`, emitting the same kind of warning/error used for existing alias collisions. Additionally, pre-seed `linksMap` with a sentinel entry for `"build"` in `createServices()` before processing service definitions so the existing collision-skip logic naturally rejects it.

### Proof of Concept
Go unit test extending `TestCreateFromServiceDefinition_AliasCollisionWarning` style tests in `executors/docker/services_test.go`:
```go
func TestCreateFromServiceDefinition_RejectsBuildAlias(t *testing.T) {
    e := &executor{}
    logs := bytes.Buffer{}
    e.BuildLogger = buildlogger.New(&common.Trace{Writer: &logs}, logrus.NewEntry(logrus.New()), buildlogger.Options{})

    linksMap := map[string]*serviceInfo{
        "build": nil, // simulate reserved alias not yet protected
    }

    imageConfig := spec.Image{Name: "attacker/image:latest", Alias: "build"}
    err := e.createFromServiceDefinition(0, imageConfig, linksMap)
    require.NoError(t, err)

    // Assert the reserved alias was never overwritten with an attacker container
    assert.Nil(t, linksMap["build"], "alias 'build' must never resolve to a user service container")
}
```
Fuzz test for `SplitNameAndVersion` per the question's proof idea:
```go
func FuzzSplitNameAndVersion_NeverProducesBuildAlias(f *testing.F) {
    f.Add("build")
    f.Add("build:latest")
    f.Add("BUILD")
    f.Fuzz(func(t *testing.T, s string) {
        svc := services.SplitNameAndVersion(s)
        for _, a := range svc.Aliases {
            assert.NotEqual(t, "build", strings.ToLower(a))
        }
    })
}
```
End-to-end PoC job (requires docker executor + `FF_NETWORK_PER_BUILD=true`): define a service with `alias: build`, then from the job script attempt to resolve/curl `http://build` and assert whether it reaches the attacker's service container rather than failing/self-resolving — this validates the actual daemon-level alias collision behavior that the unit-level check above cannot fully exercise.

### Citations

**File:** helpers/container/services/services.go (L65-73)
```go
	alias := strings.ReplaceAll(service.Service, "/", "__")
	service.Aliases = append(service.Aliases, alias)

	// Create alternative link name according to RFC 1123
	// Where you can use only `a-zA-Z0-9-`
	alternativeName := strings.ReplaceAll(service.Service, "/", "-")
	if alias != alternativeName {
		service.Aliases = append(service.Aliases, alternativeName)
	}
```

**File:** executors/docker/services.go (L50-65)
```go
func (e *executor) createServices() error {
	e.SetCurrentStage(ExecutorStageCreatingServices)
	e.BuildLogger.Debugln("Creating services...")

	servicesDefinitions, err := e.getServicesDefinitions()
	if err != nil {
		return err
	}

	linksMap := make(map[string]*serviceInfo)

	for index, serviceDefinition := range servicesDefinitions {
		if err := e.createFromServiceDefinition(index, serviceDefinition, linksMap); err != nil {
			return err
		}
	}
```

**File:** executors/docker/services.go (L168-205)
```go
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

**File:** docs/executors/docker.md (L277-277)
```markdown
The runner uses the `build` alias to resolve the job container.
```
