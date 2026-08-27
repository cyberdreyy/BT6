### Title
Unbounded on-chain allowlist array causes O(n) linear scan and full-list copy/stringification on every unprivileged Vault gateway request - ([File: core/capabilities/vault/allow_list_based_auth.go])

### Summary
Every Vault request routed through the gateway (an internet-facing, unprivileged-client-reachable path) triggers `allowListBasedAuth.AuthorizeRequest`, which calls `findAllowlistedItemWithRetry` and linearly scans the *entire* in-memory allowlist (`GetAllowlistedRequests`) to find a matching digest via `fetchAllowlistedItem`. There is no cap on how large this allowlist can grow, and the list is never pruned of expired entries in memory, so the cost of authorizing every single request grows linearly with the total number of allowlist entries ever accumulated on-chain.

### Finding Description
`allowListBasedAuth.AuthorizeRequest` ( [1](#0-0) ) is invoked for any incoming Vault gateway message that doesn't use JWT-based auth (`req.Auth == ""`), which is the default/back-compat path ( [2](#0-1) ). For each such request it calls `findAllowlistedItemWithRetry`: [3](#0-2) 

Inside this loop:
1. `r.workflowRegistrySyncer.GetAllowlistedRequests(ctx)` is called, which copies the **entire** in-memory allowlist slice on every request ( [4](#0-3) ).
2. A second full loop then builds a `[]string` of human-readable descriptions of every entry (`allowedRequestsStrs`) purely for debug logging — this happens even on the common/successful path, not just on failure.
3. `fetchAllowlistedItem` does a full linear O(n) scan over the entire list to find a matching digest ( [5](#0-4) ).

Crucially, the allowlist array is populated purely from on-chain events via `getAllowlistedRequests` in `workflow_registry.go`, which fetches "active" allowlisted requests in pages of `MaxResultsPerQuery` ( [6](#0-5) ), but nothing in `allow_list_based_auth.go` or `AuthorizeRequest` ever removes expired entries from the in-memory slice held by the authorizer's dependency, nor bounds it — the only place expiry is checked is *after* a matching digest is found ( [7](#0-6) ), not before/during the scan to skip or prune stale entries. Since the underlying array is allowed to grow unboundedly with every allowlist request any workflow owner submits on-chain over time, and this per-request authorization path is on the hot path for every unprivileged client request hitting the Vault gateway handler (`GatewayHandler.HandleGatewayMessage` → request processor → authorizer chain, see `NewGatewayHandler`, [8](#0-7) ), the CPU/memory cost of authorizing a request scales with total historical allowlist size rather than with the number of *live* entries.

This is a close structural analog to the Sherlock finding: an unbounded array (`orderbook` in the report, here the in-memory `allowListedRequests` slice) is iterated in full inside a function reachable by any unprivileged caller (`withdraw()` there, `AuthorizeRequest`/Vault request handling here), with no size cap or early-exit optimization, so the cost of the operation is attacker/growth-influenced and unbounded.

### Impact Explanation
As the allowlist grows (which happens naturally over the life of the Vault DON as more workflow owners submit allowlist requests), every single Vault request — including from legitimate but unprivileged clients — pays an increasing linear cost: a full slice copy, a full string-building pass over all entries (even for successful lookups), and a full linear scan. This degrades node responsiveness for the Vault gateway handler over time and could eventually cause request timeouts / effective denial-of-service for Vault operations (secrets create/list/delete) as the allowlist size increases, without bound. This is a performance/availability degradation issue rather than a fund-loss or auth-bypass issue.

### Likelihood Explanation
Likelihood is moderate: the allowlist naturally grows over time as more workflow owners register requests on-chain (a normal, permissionless operation not requiring special privilege beyond linking an owner), and every incoming Vault request pays the cost. There is no adversarial trigger needed beyond normal usage at scale; a moderately active Vault DON deployment would accumulate a large volume of allowlist entries over months/years since there's no evidence of eviction/pruning of expired entries from the authorizer's view.

### Recommendation
- Cap the number of retained allowlist entries kept in memory, or index them by digest (e.g., a `map[[32]byte]WorkflowRegistryOwnerAllowlistedRequest`) instead of a linear slice, so `fetchAllowlistedItem` is O(1) instead of O(n).
- Prune expired entries proactively (based on `ExpiryTimestamp`) from the in-memory syncer state, rather than only checking expiry after a match is found.
- Remove/guard the debug-only `allowedRequestsStrs` full-list stringification from the hot path (only build it when actually needed for a failure log, and consider truncating/sampling it).
- Avoid a full slice copy of the entire allowlist on every single `GetAllowlistedRequests` call; consider read-through indexed lookup instead.

### Proof of Concept
Conceptual, not exploit code (this is an efficiency/DoS class, not a fund-theft bug):
1. Over time, many distinct workflow owners submit valid on-chain `allowlist-request` calls to the `WorkflowRegistry` contract (a normal permissionless operation for onboarded owners).
2. The Vault DON's `workflowRegistry.getAllowlistedRequests` syncer continuously accumulates these into `w.allowListedRequests`, which is never trimmed in `allowListBasedAuth`.
3. Any unprivileged client sends a Vault gateway request (e.g., `secrets/create`), which flows through `GatewayHandler.HandleGatewayMessage` → `authorizer.AuthorizeRequest` → `allowListBasedAuth.AuthorizeRequest` → `findAllowlistedItemWithRetry`.
4. Each such call re-copies and linearly scans the full (unboundedly growing) allowlist array and builds a full-list debug string, so per-request latency/CPU cost scales with total historical allowlist size, degrading throughput for all Vault requests as the list grows.

### Citations

**File:** core/capabilities/vault/allow_list_based_auth.go (L34-61)
```go
func (r *allowListBasedAuth) AuthorizeRequest(ctx context.Context, req jsonrpc.Request[json.RawMessage]) (*AuthResult, error) {
	r.lggr.Debugw("AllowListBasedAuth authorizing request", "method", req.Method, "requestID", req.ID)
	requestDigest, err := req.Digest()
	if err != nil {
		r.lggr.Debugw("AllowListBasedAuth failed to create digest", "method", req.Method, "requestID", req.ID, "error", err)
		return nil, err
	}
	requestDigestBytes, err := hex.DecodeString(requestDigest)
	if err != nil {
		r.lggr.Debugw("AllowListBasedAuth failed to decode digest", "method", req.Method, "requestID", req.ID, "requestDigest", requestDigest, "error", err)
		return nil, err
	}
	requestDigestBytes32 := [32]byte(requestDigestBytes)
	if r.workflowRegistrySyncer == nil {
		r.lggr.Errorw("AllowListBasedAuth workflowRegistrySyncer is nil", "method", req.Method, "requestID", req.ID)
		return nil, errors.New("internal error: workflowRegistrySyncer is nil")
	}
	allowlistedRequest, allowedRequestsStrs, err := r.findAllowlistedItemWithRetry(ctx, req, requestDigest, requestDigestBytes32)
	if err != nil {
		return nil, err
	}
	if allowlistedRequest == nil {
		r.lggr.Debugw("AllowListBasedAuth request digest not allowlisted",
			"method", req.Method,
			"requestID", req.ID,
			"digestHexStr", requestDigest,
			"allowedRequestsStrs", allowedRequestsStrs)
		return nil, errors.New("request not allowlisted")
```

**File:** core/capabilities/vault/allow_list_based_auth.go (L64-68)
```go
	if time.Now().UTC().Unix() > int64(allowlistedRequest.ExpiryTimestamp) {
		authorizedRequestStr := string(allowlistedRequest.RequestDigest[:])
		r.lggr.Debugw("AllowListBasedAuth authorization expired", "method", req.Method, "requestID", req.ID, "authorizedRequestStr", authorizedRequestStr, "expiryTimestamp", allowlistedRequest.ExpiryTimestamp)
		return nil, errors.New("request authorization expired")
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

**File:** core/capabilities/vault/authorizer.go (L121-128)
```go
func (a *authorizer) authorizeRequest(ctx context.Context, req jsonrpc.Request[json.RawMessage]) (*AuthResult, error) {
	// Requests without req.Auth continue using the allowlist-based path for backwards compatibility.
	// Existing clients do not populate the auth field yet, so treating an empty value as JWT would break them.
	if req.Auth == "" {
		return a.authorizeAllowListBasedAuth(ctx, req)
	}
	return a.authorizeJWTBasedAuth(ctx, req)
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

**File:** core/services/workflows/syncer/v2/workflow_registry.go (L1216-1244)
```go
// GetAllowlistedRequests uses contract reader to query the contract for all allowlisted requests
func (w *workflowRegistry) getAllowlistedRequests(ctx context.Context, contractReader types.ContractReader) ([]workflow_registry_wrapper_v2.WorkflowRegistryOwnerAllowlistedRequest, *big.Int, *types.Head, error) {
	if contractReader == nil {
		return nil, nil, nil, errors.New("cannot fetch allow listed requests: nil contract reader")
	}
	contractBinding := types.BoundContract{
		Address: w.workflowRegistryAddress,
		Name:    WorkflowRegistryContractName,
	}

	// Read current total allowlisted requests
	var headAtLastRead *types.Head
	var totalAllowlistedRequestsResult *big.Int
	readIdentifier := contractBinding.ReadIdentifier(TotalAllowlistedRequestsMethodName)
	headAtLastRead, err := contractReader.GetLatestValueWithHeadData(
		ctx, readIdentifier, primitives.Unconfirmed, nil, &totalAllowlistedRequestsResult,
	)
	if err != nil {
		return []workflow_registry_wrapper_v2.WorkflowRegistryOwnerAllowlistedRequest{}, w.lastSeenAllowlistedRequestsCount, &types.Head{Height: "0"}, errors.New("failed to get latest value with head data. error: " + err.Error())
	}

	if w.lastSeenAllowlistedRequestsCount.Cmp(totalAllowlistedRequestsResult) == 0 {
		return []workflow_registry_wrapper_v2.WorkflowRegistryOwnerAllowlistedRequest{}, totalAllowlistedRequestsResult, headAtLastRead, nil
	}

	var newAllowlistedRequests []workflow_registry_wrapper_v2.WorkflowRegistryOwnerAllowlistedRequest
	readIdentifier = contractBinding.ReadIdentifier(GetActiveAllowlistedRequestsReverseMethodName)
	var endIndex = new(big.Int).Sub(totalAllowlistedRequestsResult, big.NewInt(1))
	var startIndex *big.Int
```

**File:** core/capabilities/vault/gw_handler.go (L84-126)
```go
func NewGatewayHandler(
	secretsService vaulttypes.SecretsService,
	connector gatewayConnector,
	workflowRegistrySyncer workflowsyncerv2.WorkflowRegistrySyncer,
	lggr logger.Logger,
	limitsFactory limits.Factory,
	authorizer Authorizer,
	auth0 *Auth0Config,
) (*GatewayHandler, error) {
	var jwtAuthService services.Service
	var jwtBasedAuth Authorizer
	if auth0 != nil {
		var err error
		jwtAuthService, err = NewJWTBasedAuth(JWTBasedAuthConfig{
			IssuerURL: auth0.IssuerURL,
			Audience:  auth0.Audience,
			TenantID:  auth0.TenantID,
		}, limitsFactory, lggr)
		if err != nil {
			return nil, fmt.Errorf("failed to create JWTBasedAuth: %w", err)
		}
		jwtBasedAuth = jwtAuthService.(Authorizer)
	}

	if authorizer == nil {
		allowListBasedAuth := NewAllowListBasedAuth(lggr, workflowRegistrySyncer)
		authorizer = NewAuthorizer(allowListBasedAuth, jwtBasedAuth, lggr)
	}

	requestValidator, err := NewRequestValidatorFromLimitsFactory(limitsFactory)
	if err != nil {
		return nil, fmt.Errorf("failed to create request validator: %w", err)
	}

	metrics, err := newMetrics()
	if err != nil {
		return nil, fmt.Errorf("failed to create metrics: %w", err)
	}

	requestProcessor, err := NewGatewayVaultRequestProcessor(requestValidator, authorizer, true, lggr)
	if err != nil {
		return nil, fmt.Errorf("failed to create gateway vault request processor: %w", err)
	}
```
