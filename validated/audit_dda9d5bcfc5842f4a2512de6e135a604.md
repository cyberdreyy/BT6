This confirms the design. The `allowed_images`/`allowed_services` enforcement described in the question (`VerifyAllowedImage` in `common/allowed_images.go`) is invoked with `AllowedImages`/`AllowedServices` sourced from the runner's own local `config.toml` (`e.Config.Docker.AllowedServices` in [1](#0-0) , `s.Config.Kubernetes.AllowedImages`/`AllowedServices` in [2](#0-1) ), not from any value returned by the job-router/coordinator backend. The job/spec fetched via `GetJob` only supplies the requested image/service names (`opts.image`/`service.Name`), which are checked client-side against the runner's own trusted, locally-configured allow-list before container creation.

### Title
No vulnerability - allowed_images/allowed_services enforcement is entirely client-side and independent of job-router backend selection - (File: router/client_conn_factory.go)

### Summary
The premise assumes that `allowed_images`/`allowed_services` restriction enforcement happens on the job-router (coordinator) backend and that gRPC round-robin load balancing across multiple DNS-resolved backends could route `GetJob` to a "non-enforcing" backend, bypassing this policy. In the actual code, this restriction is enforced entirely on the runner side using its own local configuration, and is applied after the job spec is received, regardless of which backend answered.

### Finding Description
`ClientConnFactory.newConn` in `router/client_conn_factory.go` does indeed use `"dns:" + hostWithPort(u)` as the dial target with `grpc.WithDefaultServiceConfig(round_robin)` for both `grpc` and `grpcs` schemes [3](#0-2) , so it's true that multiple A/AAAA records behind one DNS name could be load-balanced across by gRPC. However, the `allowed_images`/`allowed_services` invariant is not something the job-router server is expected/trusted to enforce for the runner - it's a runner-local security control. `common.VerifyAllowedImage` (`common/allowed_images.go`) is called by the executors (Docker's `getServicesDefinitions`/`verifyAllowedImage` in `executors/docker/services.go` and Kubernetes' `verifyAllowedImages` in `executors/kubernetes/kubernetes.go`) using `AllowedImages`/`AllowedServices` values read from the runner's own `config.toml` (`e.Config.Docker.AllowedServices`, `s.Config.Kubernetes.AllowedImages`), never from any field in the `GetJob` RPC response. The job spec returned by the router (whether from a "restricted" or "unrestricted" backend) only supplies the desired image/service name string; that string is validated against the runner's local, trusted allow-list at container-build time, not against any policy asserted by the router backend. Therefore even a malicious/compromised backend cannot bypass this check by omitting server-side enforcement — the check happens irrespective of the backend that served the job.

### Impact Explanation
None. The scoped impact (restriction bypass via inconsistent backend enforcement) does not exist because there's no backend-side enforcement being relied upon in the first place — enforcement is a runtime, runner-local invariant applied uniformly regardless of the job-router backend that returned the spec.

### Likelihood Explanation
Not applicable — this is not an exploitable code path since the described trust assumption (that the job-router enforces `allowed_images`/`allowed_services`) does not match the actual architecture.

### Recommendation
No fix needed for this specific concern. If defense-in-depth against a compromised/malicious job-router response is desired, ensure — as already done — that image/service name validation always occurs in the executor construction path (`executors/docker/services.go`, `executors/kubernetes/kubernetes.go`) using local config values, and never trust any "already validated" flag potentially embedded in the router's job payload.

### Proof of Concept
Not applicable given no vulnerability. A differential test could still be written to reinforce the invariant: spin up two mock `JobRouterServer` instances behind one DNS name (one returning a spec with image `evil/image:latest`, one with `allowed/image:latest`), configure the runner locally with `allowed_images = ["allowed/*"]`, and repeatedly call `RequestJob`/build execution asserting that regardless of which backend answers, jobs requesting `evil/image:latest` always fail with `common.ErrDisallowedImage` — this would pass today since enforcement is local, confirming no bypass exists.

### Citations

**File:** executors/docker/services.go (L90-90)
```go
		err := e.verifyAllowedImage(service.Name, "services", e.Config.Docker.AllowedServices, internalServiceImages)
```

**File:** executors/kubernetes/kubernetes.go (L1611-1637)
```go
func (s *executor) verifyAllowedImages(opts containerBuildOpts) error {
	// check if the image/service is allowed
	internalImages := []string{
		s.ExpandValue(s.Config.Kubernetes.Image),
		s.ExpandValue(s.helperImageInfo.Name),
	}

	var (
		optionName    string
		allowedImages []string
	)
	if opts.isServiceContainer {
		optionName = "services"
		allowedImages = s.Config.Kubernetes.AllowedServices
	} else if opts.name == buildContainerName {
		optionName = "images"
		allowedImages = s.Config.Kubernetes.AllowedImages
	}

	verifyAllowedImageOptions := common.VerifyAllowedImageOptions{
		Image:          opts.image,
		OptionName:     optionName,
		AllowedImages:  allowedImages,
		InternalImages: internalImages,
	}

	return common.VerifyAllowedImage(verifyAllowedImageOptions, s.BuildLogger)
```

**File:** router/client_conn_factory.go (L240-256)
```go
	case protocolGRPC:
		// See https://github.com/grpc/grpc/blob/master/doc/naming.md.
		addressToDial = "dns:" + hostWithPort(u)
		opts = append(opts,
			// See https://github.com/grpc/grpc/blob/master/doc/service_config.md.
			// See https://github.com/grpc/grpc/blob/master/doc/load-balancing.md.
			grpc.WithDefaultServiceConfig(`{"loadBalancingConfig":[{"round_robin":{}}]}`),
		)
	case protocolGRPCS:
		// See https://github.com/grpc/grpc/blob/master/doc/naming.md.
		addressToDial = "dns:" + hostWithPort(u)
		opts = append(opts,
			grpc.WithTransportCredentials(credentials.NewTLS(tlsConfig)),
			// See https://github.com/grpc/grpc/blob/master/doc/service_config.md.
			// See https://github.com/grpc/grpc/blob/master/doc/load-balancing.md.
			grpc.WithDefaultServiceConfig(`{"loadBalancingConfig":[{"round_robin":{}}]}`),
		)
```
