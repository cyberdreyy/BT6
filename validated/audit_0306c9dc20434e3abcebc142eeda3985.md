### Title
Unbounded linear-scan allowlist authorization enables CPU/memory exhaustion DoS on the Vault gateway - (File: core/capabilities/vault/allow_list_based_auth.go)

### Summary
Every Vault request received by the Gateway is authorized by `allowListBasedAuth.AuthorizeRequest`, which repeatedly copies and linearly scans the entire in-memory allowlist (`allowListedRequests`) with no size bound, retrying up to 11 times with 3-second sleeps between attempts. Because the allowlist grows unboundedly over time (any workflow owner can add entries via the on-chain `WorkflowRegistry`), and because every unprivileged/unauthenticated request reaching the Gateway's public user-facing endpoint triggers this O(n) work up to 11 times, an attacker can cause growing, amplified CPU/memory/goroutine consumption analogous to the `massUpdatePools()`-style "unbounded loop" DoS class.

### Finding Description
`AuthorizeRequest` calls `findAllowlistedItemWithRetry`, which for every attempt (up to `retryCount+1 = 11` times):
1. Calls `workflowRegistrySyncer.GetAllowlistedRequests(ctx)`, which allocates a **full copy** of the entire allowlist slice under an RLock: [1](#0-0) 
2. Unconditionally builds a formatted debug string (`fmt.Sprintf` + `hex.EncodeToString`) for **every single entry** in the allowlist, regardless of whether debug logging is enabled: [2](#0-1) 
3. Performs a linear scan (`fetchAllowlistedItem`) over the entire list to find a matching digest: [3](#0-2) 
4. If unmatched, sleeps 3 seconds and repeats, up to 11 total iterations: [4](#0-3) 

This authorizer is wired as the default authorization path for every Vault request the node/Gateway processes: [5](#0-4)  and [6](#0-5) . The allowlist itself has no enforced cap and is expected to grow over the DON's lifetime as workflow owners register more allowlisted requests via `syncAllowlistedRequests`, which only prunes by expiry, not by size: [7](#0-6) . There is no early-exit optimization (e.g., map-based digest lookup, or skipping string-building when the request is unauthorized), so cost per request scales linearly with allowlist size and is multiplied by up to 11 retry attempts for every request that does not immediately match — including deliberately malformed/garbage requests from unauthenticated clients.

### Impact Explanation
As the allowlist grows (a normal, expected outcome of DON operation, not requiring any special privilege beyond being a workflow owner able to register vault permission requests on-chain), the per-request authorization cost grows unboundedly. An attacker who floods the public Vault gateway endpoint with unauthenticated/garbage JSON-RPC requests forces the node to perform repeated O(n) slice copies, O(n) string formatting, and O(n) linear scans, up to 11 times per request with ~33 seconds of blocking retry sleeps, tying up goroutines and consuming CPU/memory proportional to allowlist size for every single incoming request. This degrades or denies the Vault authorization pipeline for legitimate users, mirroring the medium-severity "unbounded loop causes DoS as underlying collection grows" bug class from the referenced report.

### Likelihood Explanation
The endpoint is reachable by any unprivileged client capable of sending JSON-RPC requests to the Gateway's user-facing HTTP server; no authentication is required to trigger the authorization scan itself (that scan is what performs the authentication decision). The likelihood scales with how large the on-chain allowlist becomes over the life of the DON, which is realistic in a growing production deployment with many workflow owners.

### Recommendation
Replace the O(n) linear scan with an O(1) map lookup keyed by request digest, avoid unconditional formatted-string construction for logging (guard with a log-level check or lazy evaluation), and consider bounding/pruning the in-memory allowlist size independent of expiry, plus reducing/short-circuiting retries for clearly malformed requests.

### Proof of Concept
1. Populate the on-chain `WorkflowRegistry` allowlist with a large number of entries (organic growth over time, or by a workflow owner registering many allowlisted requests).
2. Send a stream of unauthenticated/garbage JSON-RPC Vault requests to the Gateway's public user endpoint.
3. Observe that each request triggers up to 11 full-list copies (`GetAllowlistedRequests`), 11 full-list debug-string builds, and 11 full linear scans (`fetchAllowlistedItem`), each separated by 3-second sleeps, consuming CPU/memory/goroutines proportional to allowlist size for every request, degrading service for legitimate users.

### Citations

**File:** core/services/workflows/syncer/v2/workflow_registry.go (L764-803)
```go
func (w *workflowRegistry) syncAllowlistedRequests(ctx context.Context) {
	ticker := w.getTicker(defaultTickIntervalForAllowlistedRequests)
	w.lggr.Debug("starting syncAllowlistedRequests")
	for {
		select {
		case <-ctx.Done():
			w.lggr.Debug("shutting down syncAllowlistedRequests, %s", ctx.Err())
			return
		case <-ticker:
			newAllowListedRequests, totalAllowlistedRequests, head, err := w.getAllowlistedRequests(ctx, w.contractReader)
			if err != nil {
				w.lggr.Errorw("failed to call getAllowlistedRequests", "err", err)
				continue
			}
			w.allowListedMu.Lock()
			// Prune expired requests
			activeAllowlistedRequests := []workflow_registry_wrapper_v2.WorkflowRegistryOwnerAllowlistedRequest{}
			expiredRequestsCount := 0
			for _, request := range w.allowListedRequests {
				if int64(request.ExpiryTimestamp) > time.Now().Unix() {
					activeAllowlistedRequests = append(activeAllowlistedRequests, request)
				} else {
					expiredRequestsCount++
				}
			}

			// Add new requests
			activeAllowlistedRequests = append(activeAllowlistedRequests, newAllowListedRequests...)
			w.allowListedRequests = activeAllowlistedRequests
			w.lastSeenAllowlistedRequestsCount = totalAllowlistedRequests
			w.lggr.Debugw("synced allowlisted requests",
				"newRequestsNum", len(newAllowListedRequests),
				"expiredRequestsNum", expiredRequestsCount,
				"activeRequestsNum", len(w.allowListedRequests),
				"lastSeenOnchainRequestsNum", w.lastSeenAllowlistedRequestsCount,
				"blockHeight", head.Height,
			)
			w.allowListedMu.Unlock()
		}
	}
```

**File:** core/services/workflows/syncer/v2/workflow_registry.go (L1199-1205)
```go
func (w *workflowRegistry) GetAllowlistedRequests(_ context.Context) []workflow_registry_wrapper_v2.WorkflowRegistryOwnerAllowlistedRequest {
	w.allowListedMu.RLock()
	defer w.allowListedMu.RUnlock()
	allowListedRequests := make([]workflow_registry_wrapper_v2.WorkflowRegistryOwnerAllowlistedRequest, len(w.allowListedRequests))
	copy(allowListedRequests, w.allowListedRequests)
	return allowListedRequests
}
```

**File:** core/capabilities/vault/allow_list_based_auth.go (L79-111)
```go
func (r *allowListBasedAuth) findAllowlistedItemWithRetry(ctx context.Context, req jsonrpc.Request[json.RawMessage], requestDigest string, requestDigestBytes32 [32]byte) (*workflow_registry_wrapper_v2.WorkflowRegistryOwnerAllowlistedRequest, []string, error) {
	for attempt := 0; attempt <= r.retryCount; attempt++ {
		allowedRequests := r.workflowRegistrySyncer.GetAllowlistedRequests(ctx)
		allowedRequestsStrs := make([]string, 0, len(allowedRequests))
		for _, rr := range allowedRequests {
			allowedReqStr := fmt.Sprintf("AuthorizedOwner: %s, RequestDigest: %s, ExpiryTimestamp: %d", rr.Owner.Hex(), hex.EncodeToString(rr.RequestDigest[:]), rr.ExpiryTimestamp)
			allowedRequestsStrs = append(allowedRequestsStrs, allowedReqStr)
		}
		r.lggr.Debugw("AllowListBasedAuth loaded allowlisted requests", "method", req.Method, "requestID", req.ID, "attempt", attempt+1, "allowedRequests", allowedRequestsStrs)

		allowlistedRequest := r.fetchAllowlistedItem(allowedRequests, requestDigestBytes32)
		if allowlistedRequest != nil {
			return allowlistedRequest, allowedRequestsStrs, nil
		}
		if attempt == r.retryCount {
			return nil, allowedRequestsStrs, nil
		}

		r.lggr.Debugw("AllowListBasedAuth request digest not yet allowlisted, retrying",
			"method", req.Method,
			"requestID", req.ID,
			"digestHexStr", requestDigest,
			"attempt", attempt+1,
			"maxAttempts", r.retryCount+1,
			"retryInterval", r.retryInterval)
		if err := sleepWithContext(ctx, r.retryInterval); err != nil {
			r.lggr.Debugw("AllowListBasedAuth retry canceled", "method", req.Method, "requestID", req.ID, "error", err)
			return nil, nil, err
		}
	}

	return nil, nil, nil // unreachable: loop always returns
}
```

**File:** core/capabilities/vault/allow_list_based_auth.go (L113-120)
```go
func (r *allowListBasedAuth) fetchAllowlistedItem(allowListedRequests []workflow_registry_wrapper_v2.WorkflowRegistryOwnerAllowlistedRequest, digest [32]byte) *workflow_registry_wrapper_v2.WorkflowRegistryOwnerAllowlistedRequest {
	for _, item := range allowListedRequests {
		if item.RequestDigest == digest {
			return &item
		}
	}
	return nil
}
```

**File:** core/capabilities/vault/gw_handler.go (L108-111)
```go
	if authorizer == nil {
		allowListBasedAuth := NewAllowListBasedAuth(lggr, workflowRegistrySyncer)
		authorizer = NewAuthorizer(allowListBasedAuth, jwtBasedAuth, lggr)
	}
```

**File:** core/services/gateway/handlers/vault/handler.go (L192-207)
```go
	allowListBasedAuth := vaultcap.NewAllowListBasedAuth(lggr, workflowRegistrySyncer)
	var jwtBasedAuth vaultcap.Authorizer
	var jwtAuth services.Service
	if cfg.Auth0 != nil {
		validator, err := vaultcap.NewJWTBasedAuth(vaultcap.JWTBasedAuthConfig{
			IssuerURL: cfg.Auth0.IssuerURL,
			Audience:  cfg.Auth0.Audience,
			TenantID:  cfg.Auth0.TenantID,
		}, limitsFactory, lggr)
		if err != nil {
			return nil, fmt.Errorf("failed to create JWTBasedAuth: %w", err)
		}
		jwtBasedAuth = validator
		jwtAuth = validator
	}
	authorizer := vaultcap.NewAuthorizer(allowListBasedAuth, jwtBasedAuth, lggr)
```
