## Title
Stale/duplicate allowlist entries in `WorkflowRegistry` sync can cause the Vault gateway to authorize requests against an incorrect owner - (File: `core/capabilities/vault/allow_list_based_auth.go`)

### Summary
The reported bug class is: values are appended to an array without checking for an existing/duplicate entry, and downstream logic only ever inspects the **first** matching element — which may be stale or incorrect — silently ignoring subsequent (correct) entries. The same pattern exists in the Chainlink node's Vault DON allowlist-based authentication path: `WorkflowRegistrySyncer` accumulates allowlisted-request entries into a slice with no uniqueness check on `RequestDigest`, and `allowListBasedAuth.fetchAllowlistedItem` resolves authorization by returning the **first** slice element whose digest matches, ignoring any other entries for the same digest.

### Finding Description
`workflowRegistry.syncAllowlistedRequests` periodically fetches newly-allowlisted requests from the on-chain `WorkflowRegistry` contract and appends them to the in-memory cache with no de-duplication by `RequestDigest`: [1](#0-0) 

Entries are only pruned when they *expire*; there is no check for whether an incoming entry's `RequestDigest` already exists in `w.allowListedRequests` (possibly registered by a different `Owner` or with a different `ExpiryTimestamp`). Both the old and new entries for the same digest can coexist in the slice simultaneously as long as neither has expired.

`GetAllowlistedRequests` returns a defensive copy of this raw, potentially duplicate-containing slice: [2](#0-1) 

The Vault allowlist-based authorizer then resolves a request's authorization by linearly scanning this slice and returning the **first** matching element by digest: [3](#0-2) 

This first match's `Owner` is propagated as the `workflowOwner`/`AuthorizedOwner` for the request: [4](#0-3) 

That `AuthorizedOwner` value is trusted downstream to (a) validate that a request's secret identifiers' `Owner` field matches the authorized owner, and (b) stamp/prefix the request ID and secret namespace used for storage: [5](#0-4) [6](#0-5) 

Because the sync loop never de-duplicates by digest, whichever entry happens to have been fetched/appended earliest into the in-memory slice "wins" the authorization decision for as long as it remains unexpired — even if a later, more specific/legitimate on-chain registration exists for the exact same digest with a different `Owner`. This is structurally identical to the Nested Finance bug: an array is grown by blind `append` without existence checks, and only the first (possibly stale/incorrect) entry is ever consulted by consuming logic.

### Impact Explanation
If the same `RequestDigest` can end up allowlisted on-chain under two different owner addresses while both entries are still unexpired (e.g., a race between two callers, or a stale still-valid allowlist entry from an earlier request that reused the same content/digest), `fetchAllowlistedItem`'s first-match semantics cause the Vault DON to attribute the request to the wrong `workflowOwner`. Since `AuthorizedOwner` is used both for owner-binding validation of secret identifiers and for prefixing the storage namespace/request ID, this can lead to cross-owner confusion in secret creation/update/delete/list operations — i.e., secrets being scoped to, or validated against, the wrong workflow owner. This falls under "cross-user response confusion" / authorization-owner-binding bypass.

### Likelihood Explanation
Likelihood is moderate: it requires two allowlist entries for the same digest to be simultaneously unexpired in the in-memory cache, which depends on on-chain allowlisting behavior (whether the `WorkflowRegistry` contract permits multiple registrations of the same digest by different callers) and on the sync loop's polling cadence relative to expiry windows. The vulnerable code path itself — unconditional append with no digest uniqueness enforcement, plus first-match linear scan in the authorizer — is present and reachable by any client whose vault request goes through the allowlist-based auth path (used for backward-compatible requests without a JWT, i.e., unprivileged/unauthenticated client input): [7](#0-6) 

### Recommendation
- Deduplicate `w.allowListedRequests` by `RequestDigest` when merging newly fetched entries in `syncAllowlistedRequests`, replacing any existing entry for the same digest with the latest on-chain state (or explicitly deciding a precedence rule, e.g., latest expiry/latest index wins) instead of blindly appending.
- Change `fetchAllowlistedItem` to detect and reject (or explicitly resolve) multiple matches for the same digest rather than silently returning the first one found, to avoid depending on slice ordering for security-relevant owner attribution.

### Proof of Concept
1. On-chain, allowlist digest `D` for owner `A` with expiry `T1` (future).
2. Before the syncer's next full refresh removes it, allowlist the same digest `D` for owner `B` with an expiry `T2 > T1` (also unexpired).
3. `syncAllowlistedRequests` appends both entries to `w.allowListedRequests` (see `core/services/workflows/syncer/v2/workflow_registry.go:778-793`); neither is pruned because both are still within their expiry.
4. A vault request matching digest `D` is authorized: `fetchAllowlistedItem` (`core/capabilities/vault/allow_list_based_auth.go:113-120`) iterates the slice and returns owner `A`'s entry (whichever was appended first), even though the caller intended/registered under owner `B`.
5. Downstream code (`authorizer.go`, `gateway_vault_request_processor.go`) treats the request as authorized for owner `A`, using `A`'s address for owner-binding checks and namespace/request-ID stamping — producing incorrect owner attribution for the request.

### Citations

**File:** core/services/workflows/syncer/v2/workflow_registry.go (L778-793)
```go
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

**File:** core/capabilities/vault/allow_list_based_auth.go (L70-76)
```go
	digestKey := string(allowlistedRequest.RequestDigest[:])
	r.lggr.Debugw("AllowListBasedAuth authorization succeeded", "method", req.Method, "requestID", req.ID, "authorizedRequestStr", digestKey, "owner", allowlistedRequest.Owner.Hex(), "expiryTimestamp", allowlistedRequest.ExpiryTimestamp)
	return &AuthResult{
		workflowOwner: allowlistedRequest.Owner.Hex(),
		digest:        digestKey,
		expiresAt:     int64(allowlistedRequest.ExpiryTimestamp),
	}, nil
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

**File:** core/capabilities/vault/authorizer.go (L113-116)
```go
	if ownerErr := validateSecretOwnersMatchAuthorized(req, authResult.AuthorizedOwner()); ownerErr != nil {
		a.lggr.Errorw("owner binding rejected request", "method", req.Method, "requestID", req.ID, "owner", authResult.AuthorizedOwner(), "hasAuth", req.Auth != "", "error", ownerErr)
		return nil, ownerErr
	}
```

**File:** core/capabilities/vault/authorizer.go (L121-127)
```go
func (a *authorizer) authorizeRequest(ctx context.Context, req jsonrpc.Request[json.RawMessage]) (*AuthResult, error) {
	// Requests without req.Auth continue using the allowlist-based path for backwards compatibility.
	// Existing clients do not populate the auth field yet, so treating an empty value as JWT would break them.
	if req.Auth == "" {
		return a.authorizeAllowListBasedAuth(ctx, req)
	}
	return a.authorizeJWTBasedAuth(ctx, req)
```

**File:** core/capabilities/vault/gateway_vault_request_processor.go (L240-250)
```go
	originalRequestID := req.ID
	authorizedOwner := authResult.AuthorizedOwner()
	prefixedRequestID := authorizedOwner + vaulttypes.RequestIDSeparator + originalRequestID
	req.ID = prefixedRequestID

	if err := stamp(prefixedRequestID); err != nil {
		p.lggr.Errorw("failed to stamp authorized request params", "method", req.Method, "requestID", req.ID, "error", err)
		return nil, fmt.Errorf("failed to stamp authorized request params: %w", err)
	}

	p.lggr.Debugw("authorized gateway vault request", "method", req.Method, "requestID", req.ID, "owner", authorizedOwner, "orgID", authResult.OrgID(), "workflowOwner", authResult.WorkflowOwner())
```
