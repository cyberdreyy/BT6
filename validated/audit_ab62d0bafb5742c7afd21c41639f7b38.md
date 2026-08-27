### Title
Path Traversal in Workflow Artifact HTTP Fetcher Allows Escaping the Configured Base Path - (File: core/services/workflows/syncer/v2/fetcher.go)

### Summary
`newHTTPFetcher`, created via `NewFetcherFunc`, fetches workflow binary/config artifacts by joining an operator-configured `baseURL` with the attacker-influenced `req.URL` field of a `ghcapabilities.Request`. Unlike its sibling `newFileFetcher`, which validates the final resolved path stays under `basePath` with an explicit `strings.HasPrefix` check, `newHTTPFetcher` performs no such post-join validation, allowing `../` sequences in `req.URL` to escape the intended base path.

### Finding Description
`NewFetcherFunc` builds a `FetcherFunc` from a configured `baseURL` and dispatches to `newHTTPFetcher` for `http`/`https` schemes: [1](#0-0) 

`newHTTPFetcher` cleans the incoming `req.URL` and joins it onto the base URL's path with no subsequent verification that the result remains within the base path: [2](#0-1) 

Compare this to `newFileFetcher`, which explicitly re-validates the resolved path is prefixed by `basePath` after joining/cleaning: [3](#0-2) 

The comment `// Clean the path to prevent directory traversal` in `newHTTPFetcher` is misleading: `filepath.Clean` only lexically normalizes the path but does not stop a path with leading `../../` segments from later being joined "above" the base path, since `filepath.Join` calls `Clean` on the concatenation and will resolve `..` segments upward past the base directory when there are more `..` components than the base path has segments.

`req.URL` for this fetcher is populated with the on-chain-registered `BinaryURL`/`ConfigURL` fields of a workflow registration event, which is fully controlled by the (unprivileged, off-chain) workflow owner who registers a workflow on the `WorkflowRegistry` contract: [4](#0-3) [5](#0-4) 

`NewFetcherFunc` is used as an alternate/local `FetcherFunc` that "bypasses the gateway" for fetching workflow artifacts directly over HTTP from a configured host (see `core/cmd/shell.go` wiring), so when this fetcher mode is enabled, the base path constraint is meant to scope which resources a workflow owner can retrieve from the configured artifact host. The missing bounds check breaks that scoping guarantee.

### Impact Explanation
This is directly analogous to the reported bug class: a service that signs/serves resources relative to a "base path" fails to constrain the effective final path, allowing a requester to escape the intended sandboxed prefix and retrieve arbitrary resources hosted at other paths on the same host (e.g., other tenants'/workflows' artifacts, unrelated internal endpoints served under the same domain, or files unintentionally exposed at other paths of the artifact host). In the Chainlink node, since `req.URL` (via `BinaryURL`/`ConfigURL`) is attacker-controlled from an on-chain workflow registration, any workflow owner permitted to register workflows can attempt to have the node fetch out-of-scope resources from the configured artifact host, undermining the intended path-based access boundary and enabling cross-tenant data exposure on shared artifact storage hosts.

### Likelihood Explanation
Reachability requires: (1) the node operator configuring the HTTP `FetcherFunc` (via `NewFetcherFunc` with an `http`/`https` base URL) instead of the default gateway-based `FetcherService`, and (2) an on-chain actor registering a workflow with a maliciously crafted `BinaryURL`/`ConfigURL` containing `../` sequences. Given the code comment claims traversal protection is already handled, and the analogous `newFileFetcher` does implement the correct check while `newHTTPFetcher` does not, this looks like an overlooked/incomplete mitigation rather than an intentional design decision — moderate likelihood in any deployment using this fetcher mode.

### Recommendation
After computing `fetchURL`/`u.Path` in `newHTTPFetcher`, add an explicit check (mirroring `newFileFetcher`) that the resolved path is prefixed by the original base URL's path (e.g., `strings.HasPrefix(u.Path, basePathPrefix)` after ensuring a trailing separator, or reject any resolved path that does not start with the expected prefix) and reject the request otherwise. Additionally, consider rejecting `req.URL` values containing `..` path segments outright before any joining occurs, for defense in depth.

### Proof of Concept
Given a configured base URL `http://storage.internal/tenantA/` and a malicious workflow registration where `BinaryURL = "../tenantB/secret-binary"`:
1. `newHTTPFetcher` computes `cleanPath = filepath.Clean("../tenantB/secret-binary")` → `"../tenantB/secret-binary"`.
2. `u.Path = filepath.Join("/tenantA/", "../tenantB/secret-binary")` → resolves (via `Clean`) to `/tenantB/secret-binary"`, escaping the `tenantA` scope.
3. No prefix/bounds check is performed before the HTTP GET is issued to `http://storage.internal/tenantB/secret-binary`, so the node fetches and processes another tenant's artifact.

Note: I was not able to fully verify the exact production deployment path where `NewFetcherFunc`'s HTTP mode is enabled by default (only its wiring reference in `core/cmd/shell.go` was found in search results but not its full context) — a Devin session with full repository access could confirm the exact operator configuration flag(s) that enable this fetcher mode and reproduce end-to-end.

### Citations

**File:** core/services/workflows/syncer/v2/fetcher.go (L184-196)
```go
	switch u.Scheme {
	case "file":
		// Ensure the basePath is absolute
		if !filepath.IsAbs(u.Path) {
			return nil, fmt.Errorf("basePath must be an absolute path, got: %s", u.Path)
		}
		return newFileFetcher(u.Path, lggr), nil
	case "http", "https":
		return newHTTPFetcher(baseURL, lggr), nil
	default:
		return nil, fmt.Errorf("unsupported URL scheme: %s", u.Scheme)
	}
}
```

**File:** core/services/workflows/syncer/v2/fetcher.go (L222-231)
```go
		fullPath := filepath.Clean(u.Path)

		// ensure that the incoming request URL is either relative or absolute but within the basePath
		if !filepath.IsAbs(fullPath) {
			// If it's not absolute, we assume it's relative to the basePath
			fullPath = filepath.Join(basePath, fullPath)
		}
		if !strings.HasPrefix(fullPath, basePath+string(filepath.Separator)) && fullPath != basePath {
			return nil, fmt.Errorf("request URL %s is not within the basePath %s", fullPath, basePath)
		}
```

**File:** core/services/workflows/syncer/v2/fetcher.go (L243-261)
```go
func newHTTPFetcher(baseURL string, lggr logger.Logger) types.FetcherFunc {
	client := &http.Client{
		Timeout: 30 * time.Second,
	}

	return func(ctx context.Context, messageID string, req ghcapabilities.Request) ([]byte, error) {
		// Clean the path to prevent directory traversal
		cleanPath := strings.TrimPrefix(filepath.Clean(req.URL), "/")

		// Join base URL with path
		u, err := url.Parse(baseURL)
		if err != nil {
			return nil, fmt.Errorf("failed to parse base URL: %w", err)
		}

		u.Path = filepath.Join(u.Path, cleanPath)
		fetchURL := u.String()

		lggr.Debugw("Fetching HTTP resource", "url", fetchURL, "workflowID", req.WorkflowID)
```

**File:** core/services/workflows/artifacts/v2/store.go (L173-182)
```go
	req := ghcapabilities.Request{
		URL:              binaryURL,
		Method:           http.MethodGet,
		MaxResponseBytes: safeUint32(uint64(maxBinarySize)),
		WorkflowID:       workflowID,
	}
	binary, err = h.fetchFn(ctx, messageID(binaryURL, workflowID), req)
	if err != nil {
		return nil, nil, &types.ArtifactFetchError{ArtifactType: "binary", URL: binaryURL, Err: err}
	}
```

**File:** core/services/workflows/syncer/v2/handler.go (L776-780)
```go
	// With Workflow Registry contract v2 the BinaryURL and ConfigURL are expected to be identifiers that put through the Storage Service.
	decodedBinary, config, err := h.workflowArtifactsStore.FetchWorkflowArtifacts(ctx, wfID, payload.BinaryURL, payload.ConfigURL)
	if err != nil {
		return nil, err
	}
```
