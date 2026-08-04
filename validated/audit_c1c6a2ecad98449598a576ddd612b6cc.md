## Finding: Confirmed underlying root cause, with partial verification

I was able to confirm the core premise of the question — reserved container names are not excluded from job-controlled service alias assignment — but ran out of tool budget before fully verifying how the final pod container list is assembled (whether Kubernetes API rejects a duplicate `"helper"` container name at pod-creation time, or whether the malicious service silently overwrites/replaces the trusted helper container definition before that point). I'll present what is confirmed and flag the remaining uncertainty explicitly.

### Title
Job-controlled service alias can collide with reserved container names (`helper`/`build`), corrupting the pull-policy/pull-retry state keyed by container name - (File: `executors/kubernetes/kubernetes.go`, `executors/kubernetes/internal/pull/manager.go`)

### Summary
`pull.manager` in `executors/kubernetes/internal/pull/manager.go` stores per-container pull policies and failure counters in plain `map[string]...` keyed only by the raw container-name string, with no notion of "trusted" vs "job-controlled" containers. [1](#0-0) 
Separately, `getServiceName` in `executors/kubernetes/kubernetes.go`, which derives a service container's name from the job-supplied alias, does not exclude the reserved names `build`, `helper`, or `init-permissions` from being chosen, and the `usedAliases` set that guards against collisions is seeded empty (only populated as other services are processed), not pre-seeded with the reserved container names. [2](#0-1) 

### Finding Description
The reserved container names are defined as constants: [3](#0-2) 

`prepareOptions`/`getServiceDefinition`/`getServiceName` assign a service's container name purely from `svc.Aliases()` filtered only by DNS-1123 validity and prior alias reuse — nothing checks against `buildContainerName`, `helperContainerName`, or `initPermissionContainerName`: [4](#0-3) 

This matches the documented behavior that a valid DNS-label alias not already used by another *service* becomes the container name (docs make no mention of reserving `build`/`helper`).

Once a service is assigned the name `"helper"` in `s.options.Services["helper"]`, `preparePullManager` builds the container→pull-policy map by first seeding it with the three reserved names (using the runner's own `s.options.Image.PullPolicies` for `helperContainerName`), and then **iterating and overwriting** with each service's own declared `PullPolicies`: [5](#0-4) 

Because the loop runs after the reserved-name seed and uses the same map key `"helper"`, the attacker-declared service's `PullPolicies` silently replace the trusted helper container's pull policy configuration before `pull.NewPullManager` is even constructed: [6](#0-5) 

At runtime, `manager.UpdatePolicyForContainer` and `markPullFailureFor`/`GetPullPolicyFor` operate purely on `imagePullErr.Container`, which is the Kubernetes-reported container name from the pod's container status — an event about any container named `"helper"` (whichever one the k8s scheduler actually attaches that status to) will increment/consult the single shared `failureMap["helper"]` and `pullPolicies["helper"]` entry: [7](#0-6) 

There is no code anywhere in the reviewed path (`getServiceName`, `prepareOptions`, `preparePullManager`, `pull.manager`) that distinguishes a "reserved" container identity from a "job-controlled" one — isolation depends entirely on the container-name string being unique, and that uniqueness invariant is not enforced against the reserved set.

**What I could not verify:** whether the actual list of `api.Container` objects assembled for the pod spec (separate from the pull-policy map) also collapses on the same string key (in which case the real helper container might be dropped/replaced entirely — a more severe issue than just failure-counter cross-contamination), or whether the Kubernetes API server would instead reject pod creation for duplicate container names, which would turn this into a denial-of-service on Prepare rather than counter cross-contamination. I did not locate and read the exact function that assembles `pod.Spec.Containers` from `s.options.Services` plus the build/helper containers within the available budget.

### Impact Explanation
At minimum, this is a confirmed break of the invariant "pull-retry state must be isolated per container role": an unprivileged job author can set a service `alias: helper`, and this makes the job's own declared `pull_policy` for that service silently override the pull policy the runner otherwise assigns to its trusted helper image, and causes the shared `failureMap`/`pullPolicies` entries for `"helper"` to be shaped/consumed by the attacker's service. Whether this escalates further (helper container definition itself being replaced, or pod creation being rejected as DoS) is unconfirmed.

### Likelihood Explanation
Trivial precondition: any GitLab CI job author can set `services: [{name: some-image, alias: helper}]` — no special permissions required, and `helper` passes the DNS-1123 label validity check just like any other name. This is fully reachable via ordinary `.gitlab-ci.yml` job configuration.

### Recommendation
In `getServiceName` (`executors/kubernetes/kubernetes.go`), reject or skip aliases matching the reserved container names (`buildContainerName`, `helperContainerName`, `initPermissionContainerName`, and the `stepsBootstrapInitContainerName`/`svc-` prefix) the same way collisions with `usedAliases` are currently skipped — i.e., pre-seed `usedAliases` with the reserved constants before processing job-supplied aliases, so a service alias can never shadow a runner-owned container name.

### Proof of Concept
Go unit test in `executors/kubernetes/kubernetes_test.go` targeting `getServiceName`/`prepareOptions`:
```go
func TestGetServiceName_RejectsReservedContainerNames(t *testing.T) {
    usedAliases := map[string]struct{}{}
    svc := &spec.Image{Name: "attacker/image", Alias: "helper"}
    name := getServiceName(svc, usedAliases)
    assert.NotEqual(t, "helper", name, "service alias must not collide with reserved helper container name")
}
```
Expected current (buggy) behavior: `name == "helper"`. Expected fixed behavior: `name` falls back to `svc-N` because `"helper"` is treated as already reserved/used.

A complementary integration-style test would build `s.options` via `prepareOptions` with a service `alias: helper`, call `preparePullManager`, and assert `k8sPullPoliciesPerContainer[helperContainerName]` still equals the pull policies derived from `s.options.Image.PullPolicies` (the build image), not the service's declared `PullPolicies` — currently this assertion fails.

### Citations

**File:** executors/kubernetes/internal/pull/manager.go (L27-41)
```go
type manager struct {
	logger       pullLogger
	pullPolicies map[string][]api.PullPolicy

	mu         sync.Mutex
	failureMap map[string]int
}

func NewPullManager(pullPolicies map[string][]api.PullPolicy, logger pullLogger) Manager {
	return &manager{
		pullPolicies: pullPolicies,
		failureMap:   map[string]int{},
		logger:       logger,
	}
}
```

**File:** executors/kubernetes/internal/pull/manager.go (L60-93)
```go
func (m *manager) UpdatePolicyForContainer(attempt int, imagePullErr *ImagePullError) bool {
	pullPolicy, _ := m.GetPullPolicyFor(imagePullErr.Container)

	m.markPullFailureFor(imagePullErr.Container)

	m.logger.Warningln(fmt.Sprintf(
		"Failed to pull image %q for container %q with policy %q: %v",
		imagePullErr.Image,
		imagePullErr.Container,
		pullPolicy,
		imagePullErr.Message,
	))

	nextPullPolicy, errPull := m.GetPullPolicyFor(imagePullErr.Container)
	if errPull == nil {
		m.logger.Infoln(fmt.Sprintf(
			"Attempt #%d: Trying %q pull policy for %q image for container %q",
			attempt+1,
			nextPullPolicy,
			imagePullErr.Image,
			imagePullErr.Container,
		))
		return true
	}

	return false
}

// markPullFailureFor informs of a failure to pull the specified image
func (m *manager) markPullFailureFor(container string) {
	m.mu.Lock()
	defer m.mu.Unlock()

	m.failureMap[container]++
```

**File:** executors/kubernetes/kubernetes.go (L62-65)
```go
const (
	buildContainerName          = "build"
	helperContainerName         = "helper"
	initPermissionContainerName = "init-permissions"
```

**File:** executors/kubernetes/kubernetes.go (L489-506)
```go
	dockerPullPoliciesPerContainer := map[string][]common.DockerPullPolicy{
		buildContainerName:          s.options.Image.PullPolicies,
		helperContainerName:         s.options.Image.PullPolicies,
		initPermissionContainerName: s.options.Image.PullPolicies,
	}
	// Concrete's container set differs from legacy: it has an
	// init-steps-bootstrap init container (running the helper image) and no
	// helper container. The pull manager keys its retry cursor per container
	// name, so the bootstrap container must be registered here for pull-retry
	// to cover helper-image pull failures. This is neutral pull-policy
	// plumbing, not script-execution logic, so the conditional is retained
	// deliberately rather than forked into a separate builder.
	if s.Build.UseNativeSteps() {
		dockerPullPoliciesPerContainer[stepsBootstrapInitContainerName] = s.options.Image.PullPolicies
	}
	for containerName, service := range s.options.Services {
		dockerPullPoliciesPerContainer[containerName] = service.PullPolicies
	}
```

**File:** executors/kubernetes/kubernetes.go (L529-532)
```go
		k8sPullPoliciesPerContainer[containerName] = k8sPullPolicies
	}

	return pull.NewPullManager(k8sPullPoliciesPerContainer, &s.BuildLogger), nil
```

**File:** executors/kubernetes/kubernetes.go (L3371-3429)
```go
func (s *executor) prepareOptions(build *common.Build) {
	index := 0
	usedAliases := make(map[string]struct{})
	s.options = &kubernetesOptions{
		Image:    build.Image,
		Services: make(map[string]*spec.Image),
	}

	for _, svc := range s.Config.Kubernetes.GetExpandedServices(s.Build.GetAllVariables()) {
		if svc.Name == "" {
			continue
		}

		serviceName, service := "", svc.ToImageDefinition()
		index, serviceName = s.getServiceDefinition(&service, usedAliases, index)
		s.options.Services[serviceName] = &service
	}

	for _, service := range build.Services {
		if service.Name == "" {
			continue
		}

		serviceName := ""
		index, serviceName = s.getServiceDefinition(&service, usedAliases, index)
		s.options.Services[serviceName] = &service
	}
}

func (s *executor) getServiceDefinition(
	service *spec.Image,
	usedAliases map[string]struct{},
	serviceIndex int,
) (int, string) {
	name := getServiceName(service, usedAliases)
	if name == "" {
		name = fmt.Sprintf("%s%d", serviceContainerPrefix, serviceIndex)
		serviceIndex++
	}

	return serviceIndex, name
}

func getServiceName(svc *spec.Image, usedAliases map[string]struct{}) string {
	for _, alias := range svc.Aliases() {
		if _, ok := usedAliases[alias]; ok {
			continue
		}
		if len(validation.IsDNS1123Label(alias)) != 0 {
			usedAliases[alias] = struct{}{}
			continue
		}

		usedAliases[alias] = struct{}{}
		return alias
	}

	return ""
}
```
