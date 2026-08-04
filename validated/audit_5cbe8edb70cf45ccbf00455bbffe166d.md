### Title
Job-supplied service alias `kubernetes` overrides in-pod hostname resolution of the Kubernetes API server via HostAliases - ([File: executors/kubernetes/host_aliases.go])

### Summary
`createServicesHostAlias` validates job-supplied service aliases only with `dns.ValidateDNS1123Subdomain`, with no denylist for cluster-reserved names such as `kubernetes`. Because `createHostAliases` places the resulting `127.0.0.1` entry first in the pod's `HostAliases`, and Kubernetes merges `HostAliases` into `/etc/hosts` (which glibc's default `nsswitch` order consults before DNS), a job that declares a service aliased `kubernetes` can make the build/helper containers resolve the hostname `kubernetes` to `127.0.0.1` instead of the real API server ClusterIP.

### Finding Description
In `executors/kubernetes/host_aliases.go`, `createServicesHostAlias` iterates a job's declared services and their aliases (`srv.Aliases()`), validating each with `dns.ValidateDNS1123Subdomain` [1](#0-0) . The string `kubernetes` is a syntactically valid DNS-1123 label, so it passes validation and is appended to `hostnames`, which become `api.HostAlias{IP: "127.0.0.1", Hostnames: hostnames}` [2](#0-1) . `createHostAliases` then prepends this service-derived alias entry ahead of any user-supplied `hostAliases`, explicitly noting that `/etc/hosts` resolution is first-come-first-served [3](#0-2) . There is no check anywhere in this path that rejects or filters cluster-critical/reserved hostnames like `kubernetes`, `kubernetes.default`, `localhost`, etc. The only rejection path is DNS-1123 syntax validity, not semantic reservation [4](#0-3) , confirmed by `TestCreateHostAliases`, which does not test for reserved-name rejection at all.

The attacker input is fully job-controlled: a `.gitlab-ci.yml` `services:` entry with `alias: kubernetes` (or `alias: [kubernetes]`) is enough to reach `createServicesHostAlias` -> `createHostAliases` -> pod spec's `HostAliases` field, which Kubernetes writes into the pod's `/etc/hosts`.

### Impact Explanation
Within the attacker's own job pod, any process (build script, CI tooling, `kubectl`, custom automation) that resolves the bare hostname `kubernetes` — as opposed to using the `KUBERNETES_SERVICE_HOST`/`KUBERNETES_SERVICE_PORT` env vars or the FQDN `kubernetes.default.svc.cluster.local` — will be redirected to `127.0.0.1` inside that same pod. This can break API access for tooling relying on that hostname or, if the attacker also runs a listener on `127.0.0.1` in a sibling container/process within the pod, intercept traffic and any service-account token sent to what the tool believes is the real API server. As stated in the question, this is confined to the attacker's own pod (self-inflicted denial of service or self-directed interception of their own credentials), unless downstream automation makes trust assumptions based on the hostname resolving correctly, which is an assumption of the job's own tooling, not a cross-tenant or cross-job compromise.

### Likelihood Explanation
Trivial to trigger: any job author can add a `services:` block with `alias: kubernetes` to their own `.gitlab-ci.yml`. No special executor configuration, privileges, or cluster access beyond being a normal pipeline author is needed. It is fully reproducible on any Kubernetes executor configuration that honors service aliases.

### Recommendation
In `createServicesHostAlias` (and/or `createHostAliases`), reject or filter alias values matching cluster-reserved hostnames (e.g., `kubernetes`, `kubernetes.default`, `kube-dns`, `localhost`) in addition to the existing DNS-1123 syntax check, returning an error similar to `invalidHostAliasDNSError` so the job fails fast with a clear message instead of silently shadowing the API server hostname.

### Proof of Concept
Add a case to `TestCreateHostAliases` in `executors/kubernetes/host_aliases_test.go`:
```go
"rejects reserved cluster hostname kubernetes": {
    services: spec.Services{
        {Name: "test-service", Alias: "kubernetes"},
    },
    expectedError: &invalidHostAliasDNSError{}, // or a new reserved-hostname error
},
```
Run `go test ./executors/kubernetes/... -run TestCreateHostAliases`. Currently this passes through and yields `HostAliases: [{IP: "127.0.0.1", Hostnames: ["test-service", "kubernetes"]}]` rather than an error, demonstrating the missing reserved-name check.

### Citations

**File:** executors/kubernetes/host_aliases.go (L39-49)
```go
	// The order that we add host aliases matter here. The host file resolves
	// host on a firs-come-first-served basis. We always want to have the
	// service host aliases first so it resolves to that ip.
	var allHostAliases []api.HostAlias
	if servicesHostAlias != nil {
		allHostAliases = append(allHostAliases, *servicesHostAlias)
	}
	allHostAliases = append(allHostAliases, hostAliases...)

	return allHostAliases, nil
}
```

**File:** executors/kubernetes/host_aliases.go (L73-80)
```go
		for _, alias := range srv.Aliases() {
			err := dns.ValidateDNS1123Subdomain(alias)
			if err != nil {
				return nil, &invalidHostAliasDNSError{service: srv, inner: err, alias: alias}
			}

			hostnames = append(hostnames, alias)
		}
```

**File:** executors/kubernetes/host_aliases.go (L83-88)
```go
	// no service hostnames to add to aliases
	if len(hostnames) == 0 {
		return nil, nil
	}

	return &api.HostAlias{IP: "127.0.0.1", Hostnames: hostnames}, nil
```

**File:** executors/kubernetes/host_aliases_test.go (L182-193)
```go
		"ignores non RFC1123 service aliases": {
			services: spec.Services{
				{
					Name:  "test-service",
					Alias: "INVALID_ALIAS",
				},
				{
					Name: "docker:dind",
				},
			},
			expectedError: &invalidHostAliasDNSError{},
		},
```
