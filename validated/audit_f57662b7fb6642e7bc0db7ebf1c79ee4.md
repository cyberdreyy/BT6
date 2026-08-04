### Title
Unsanitized `requestedURI` path-traversal in proxy handler allows escaping the resolved service/port scope in Kubernetes service-proxy requests - ([File: session/session.go], [File: executors/kubernetes/service_proxy.go])

### Summary
`parseProxyParams` (actually implemented in `session/session.go`, not `session/proxy/proxy.go`) splits the proxy URL path but performs no validation on the trailing `requestedURI` segment. `requestedURI` is passed unmodified through `Requester.ProxyRequest` → `serviceEndpointRequest` → `rest.Request.Suffix(requestedURI)`, and client-go's `Suffix`/`URL()` build the final REST path with `path.Join`, which lexically collapses `..` across the whole joined path — including the namespace/resource/name/subresource segments that were supposed to be fixed by `Settings.PortByNameOrNumber`/`ServiceName`. A sufficiently crafted `requestedURI` with `../` sequences can therefore rewrite the outgoing Kubernetes API request path to target a completely different namespace, resource kind, or object, executed under the Runner's own ServiceAccount credentials.

### Finding Description
`s.proxyHandler` derives `serviceName`, `port`, `requestedURI` from the client-supplied URL path via `parseProxyParams`, which only validates that `service` and `port` are non-empty; the third segment (`requestedURI`) is accepted as-is with no character/traversal filtering: [1](#0-0) 

`port` is safely resolved through `Settings.PortByNameOrNumber`, and `ServiceName` comes from the fixed `Settings` struct, so those two values are correctly scoped: [2](#0-1) 

However `requestedURI` is forwarded untouched into `serviceEndpointRequest`, which builds the Kubernetes REST request via `RESTClient().Verb(...).Namespace(...).Resource("services").SubResource("proxy").Name(scheme:svc:port).Suffix(requestedURI)`: [3](#0-2) 

`rest.Request.Suffix` (client-go) stores the raw suffix and the final path is assembled in `URL()` by joining `resourceName`, `subresource`, and the suffix path components with `path.Join`, which internally calls `path.Clean`. `path.Clean` resolves `..` lexically against *all* preceding joined segments — not just the suffix — meaning an attacker who supplies enough `../` sequences in `requestedURI` can strip away the `namespaces/<ns>/services/<scheme:name:port>/proxy` prefix entirely and substitute an arbitrary absolute API path (e.g. `/api/v1/namespaces/<other-ns>/pods/<pod>/log` or `/api/v1/secrets`). Because the namespace and resource-name segments the attacker needs to "peel back" are deterministic and known to the job author (they define the `services:` config for their own job), the required traversal depth is fully computable client-side, not blind. The existing test suite only validates additive behavior (`"Adds the requested URI to the proxy path"`), confirming `requestedURI` is concatenated as a raw path suffix with no sanitization step anywhere in the flow: [4](#0-3) 

No component in this call chain — `parseProxyParams`, `PortByNameOrNumber`, `ProxyRequest`, or `serviceEndpointRequest` — checks `requestedURI` for `..`, encoded traversal (`%2e%2e`), or absolute-path markers before it becomes part of the REST path prefix. The only gate is `Settings.PortByNameOrNumber`, which only validates the second path segment (`port`), not the third (`requestedURI`), so it cannot prevent this collapse of the resource path.

### Impact Explanation
Because the outgoing HTTP request path is fully resolved/cleaned on the client side before being sent to the Kubernetes API server, the resulting request is indistinguishable from a legitimate call to whatever resource the traversal resolves to. It is authorized using the Runner's own ServiceAccount credentials (the `kubeClient` used by the executor), not scoped to the specific service/port the job declared. If that ServiceAccount has RBAC permissions beyond `services/proxy` on the declared service (commonly true, since Runner's ServiceAccount typically needs `pods/exec`, `pods/log`, `secrets get`, etc. to operate normally), a job holding only its own session Token/Endpoint can use the proxy subresource as a pivot to reach other namespaces/resources/objects that `Settings.Ports` was meant to exclude — directly violating the stated invariant that `PortByNameOrNumber`/`Settings` is the sole authority for what is reachable.

### Likelihood Explanation
Preconditions are minimal and fully attacker-controlled: any job with a `services:` definition gets a session Token/Endpoint and can freely choose the `requestedURI` segment of the proxy URL sent to its own Runner session endpoint. No special privilege beyond being a normal pipeline author is required, and the namespace/resource-name values needed to compute the correct traversal depth are either already known to the job author (their own `services:` config) or discoverable via error messages/behavioral probing. This is a deterministic, repeatable bug, not a race condition or timing-dependent issue.

### Recommendation
Reject or clean `requestedURI` before use: after decoding, apply `path.Clean`/`filepath.Clean` and reject any result containing `..` elements, reject values that resolve outside a single relative sub-path (e.g., disallow leading `/`, NUL bytes, and CR/LF), and defensively verify (post-clean) that the final Kubernetes REST URL still contains the exact `namespaces/<ns>/services/<scheme:serviceName:port>/proxy` prefix that `Settings`/`PortByNameOrNumber` resolved, rejecting the request otherwise.

### Proof of Concept
Go unit test extending `TestProxyRequestHTTP` in `executors/kubernetes/service_proxy_test.go`:
```go
"Path traversal escapes the resolved service/port scope": {
    podStatus:    api.PodRunning,
    requestedURI: "../../../../../../../pods/other-pod/log", // depth tuned to known prefix length
    proxySettings: defaultProxySettings,
    endpointURI:  "/api/" + version + "/namespaces/" + objectInfo.Namespace + "/pods/other-pod/log",
    expectedStatusCode: http.StatusOK, // demonstrates request landed on a different resource type
},
```
Assert that `req.URL.Path` sent to the fake Kubernetes HTTP client equals a path outside `.../services/.../proxy`, proving the traversal escaped the intended resource scope despite `Settings.PortByNameOrNumber` having resolved a valid, in-scope port. A complementary fuzz test over `parseProxyParams`+`serviceEndpointRequest` should assert `strings.Contains(resultURL.Path, expectedServicePrefix)` for all generated `requestedURI` fuzz inputs containing `..`, `%2e%2e`, NUL, and CRLF sequences.

### Citations

**File:** session/session.go (L279-288)
```go
func parseProxyParams(path string) (service string, port string, uri string, ok bool) {
	p := strings.SplitN(path, "/", 3)
	switch len(p) {
	case 2:
		return p[0], p[1], "", p[0] != "" && p[1] != ""
	case 3:
		return p[0], p[1], p[2], p[0] != "" && p[1] != ""
	}
	return "", "", "", false
}
```

**File:** session/proxy/proxy.go (L50-60)
```go
func (p *Settings) PortByNameOrNumber(portNameOrNumber string) (Port, error) {
	intPort, _ := strconv.Atoi(portNameOrNumber)

	for _, port := range p.Ports {
		if port.Number == intPort || port.Name == portNameOrNumber {
			return port, nil
		}
	}

	return Port{}, errors.New("invalid port")
}
```

**File:** executors/kubernetes/service_proxy.go (L84-102)
```go
func (s *executor) serviceEndpointRequest(
	verb, serviceName, requestedURI string,
	port proxy.Port,
) (*rest.Request, error) {
	scheme, err := port.Scheme()
	if err != nil {
		return nil, err
	}

	// kubeAPI: ignore
	result := s.kubeClient.CoreV1().RESTClient().Verb(verb).
		Namespace(s.pod.Namespace).
		Resource("services").
		SubResource("proxy").
		Name(k8net.JoinSchemeNamePort(scheme, serviceName, strconv.Itoa(port.Number))).
		Suffix(requestedURI)

	return result, nil
}
```

**File:** executors/kubernetes/service_proxy_test.go (L181-188)
```go
		"Adds the requested URI to the proxy path": {
			podStatus:          api.PodRunning,
			requestedURI:       "foobar",
			proxySettings:      defaultProxySettings,
			endpointURI:        proxyEndpointURI + "/foobar",
			expectedBody:       defaultBody,
			expectedStatusCode: http.StatusOK,
		},
```
