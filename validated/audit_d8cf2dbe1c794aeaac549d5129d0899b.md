### Title
Unbounded per-request linear scan over the on-chain Vault allowlist enables gateway-request-triggered CPU/latency DoS - (File: core/capabilities/vault/allow_list_based_auth.go)

### Summary
`allowListBasedAuth.AuthorizeRequest`, invoked for every unauthenticated Vault request forwarded by the internet-facing gateway to `GatewayHandler.HandleGatewayMessage`, retries up to `allowListBasedAuthRetryCount+1` (11) times, and on each attempt performs a full linear scan (`fetchAllowlistedItem`) over the entire in-memory allowlist snapshot returned by `GetAllowlistedRequests`. That allowlist is populated by `AllowlistRequest` on the on-chain `WorkflowRegistry` contract, which any linked workflow owner can call permissionlessly (see `UserAllowlistRequestOp` / "user-allowlist-request-op", callable via `UserAllowlistRequest` changeset with no privileged-role check beyond having a linked owner). This mirrors the C4 `SurplusGuildMinter.getReward()` bug class: an attacker-growable collection is iterated in full on every unprivileged request path, with no cap on the collection size and no early-exit/indexing structure.

### Finding Description
- The gateway entry point `gateway.ProcessRequest` / `GatewayHandler.HandleGatewayMessage` accepts unauthenticated JSON-RPC Vault requests from any client via the gateway and routes `MethodSecretsCreate/Update/Delete/List` through `h.requestProcessor.ProcessRequest`, which calls the configured `Authorizer` (default: `allowListBasedAuth`), as seen in `core/capabilities/vault/gw_handler.go:180-224`. [1](#0-0) 
- `allowListBasedAuth.AuthorizeRequest` computes a request digest and calls `findAllowlistedItemWithRetry`, which loops `attempt := 0; attempt <= r.retryCount` (default 10, i.e. 11 iterations) and on every iteration fetches the *entire* current allowlist via `r.workflowRegistrySyncer.GetAllowlistedRequests(ctx)` and scans it linearly with `fetchAllowlistedItem`. [2](#0-1) 
- `GetAllowlistedRequests` on the syncer simply copies and returns the full slice `w.allowListedRequests`, with no size cap: [3](#0-2) 
- The allowlist is grown by on-chain calls to `AllowlistRequest` on `WorkflowRegistry`, exposed to node operators/users through the `UserAllowlistRequest` changeset ("allowlistRequest on workflow registry v2 as user") — i.e., it is intended to be called by ordinary linked workflow owners, not just privileged admins, with essentially no upper bound enforced client-side beyond gas cost of the on-chain call: [4](#0-3) [5](#0-4) 

As the number of active (non-expired) allowlisted requests grows, every unprivileged gateway request that hits `MethodSecretsCreate/Update/Delete/List` pays an O(N) cost per retry attempt, up to 11×N per request, exactly analogous to the unbounded `claimRewards` loop in the referenced report where every user action paid a cost proportional to an attacker/community-growable set size.

### Impact Explanation
As the allowlist grows (each entry is a cheap on-chain transaction, no coordination/quorum required unlike the LendingTermOnboarding governance path in the original report), the CPU cost of *every* Vault secrets request handled by the gateway/node increases linearly, and with the built-in 11x retry multiplier the cost compounds quickly. This degrades the responsiveness of the node's Vault capability for all legitimate users and, in the worst case (very large allowlist, e.g., attacker submits large numbers of cheap on-chain allowlist entries or entries accumulate over the lifetime of the network without pruning), can cause request timeouts/latency spikes across the DON's Vault handling pipeline — a denial-of-service impacting the internet-facing gateway path, not merely the on-chain contract.

### Likelihood Explanation
Likelihood is comparable to the original finding: any user permitted to link an owner and call `AllowlistRequest` can grow the list without needing majority/quorum decisions (unlike GUILD voting in the C4 report), making growth attacker-controllable at the cost of on-chain gas per allowlist entry. There is no evidence in the reachable code of pruning of expired entries or any cap (`MaxResultsPerQuery` limits pagination when querying the contract, but does not cap the resulting in-memory list size). This makes the growth path more directly attacker-controlled than the original finding, though gas cost for spamming on-chain allowlist entries is a mitigating economic factor.

### Recommendation
- Replace the linear scan in `fetchAllowlistedItem` with an indexed lookup (e.g., a `map[[32]byte]WorkflowRegistryOwnerAllowlistedRequest` keyed by `RequestDigest`), maintained by the syncer alongside/instead of the slice, so lookup is O(1) regardless of allowlist size.
- Cap and/or prune the allowlist snapshot the syncer keeps in memory (e.g., drop already-expired entries eagerly, and enforce a maximum in-memory allowlist size independent of on-chain growth).
- Reconsider the retry loop (`allowListBasedAuthRetryCount = 10`) which multiplies the per-request cost of any expensive lookup by up to 11x; make it bounded strictly by wall-clock deadlines and short-circuit as soon as a cheap indexed lookup finds a match.

### Proof of Concept
Not independently reproducible without the deployed `WorkflowRegistry` contract and a running gateway/DON, but the code path is deterministic:
1. Attacker links an owner via `LinkOwner` and repeatedly calls `AllowlistRequest(digest, expiry)` on the on-chain `WorkflowRegistry` for many distinct digests (bounded only by gas spend), growing `w.allowListedRequests` to N entries.
2. Any client sends a `MethodSecretsList`/`MethodSecretsCreate` request through the gateway to `GatewayHandler.HandleGatewayMessage`.
3. `allowListBasedAuth.AuthorizeRequest` → `findAllowlistedItemWithRetry` iterates up to 11 times, each time calling `GetAllowlistedRequests` (returns/copies all N entries) and `fetchAllowlistedItem` (O(N) scan), yielding up to 11×N digest comparisons per single incoming request, for every request processed by the node — including from users with no relationship to the attacker's allowlist entries.

### Citations

**File:** core/capabilities/vault/gw_handler.go (L187-211)
```go
	switch req.Method {
	case vaulttypes.MethodSecretsCreate, vaulttypes.MethodSecretsUpdate:
		publicKey, pkErr := h.getMasterPublicKey(ctx)
		if pkErr != nil {
			response = h.gatewayErrorResponse(ctx, gatewayID, req, pkErr)
			break
		}
		authorized, pipelineErr := h.requestProcessor.ProcessRequest(ctx, req, publicKey)
		if pipelineErr != nil {
			response = h.gatewayErrorResponse(ctx, gatewayID, req, pipelineErr)
			break
		}
		authResult = authorized.AuthResult
	case vaulttypes.MethodSecretsDelete, vaulttypes.MethodSecretsList:
		authorized, pipelineErr := h.requestProcessor.ProcessRequest(ctx, req, nil)
		if pipelineErr != nil {
			response = h.gatewayErrorResponse(ctx, gatewayID, req, pipelineErr)
			break
		}
		authResult = authorized.AuthResult
	case vaulttypes.MethodPublicKeyGet:
		response = h.handlePublicKeyGet(ctx, gatewayID, req)
	default:
		response = h.errorResponse(ctx, gatewayID, req, api.UnsupportedMethodError, errors.New("unsupported method: "+req.Method))
	}
```

**File:** core/capabilities/vault/allow_list_based_auth.go (L79-119)
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

func (r *allowListBasedAuth) fetchAllowlistedItem(allowListedRequests []workflow_registry_wrapper_v2.WorkflowRegistryOwnerAllowlistedRequest, digest [32]byte) *workflow_registry_wrapper_v2.WorkflowRegistryOwnerAllowlistedRequest {
	for _, item := range allowListedRequests {
		if item.RequestDigest == digest {
			return &item
		}
	}
	return nil
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

**File:** deployment/cre/workflow_registry/v2/changeset/operations/contracts/user_workflow_registry_ops.go (L345-387)
```go
type UserAllowlistRequestOpInput struct {
	RequestDigest   [32]byte `json:"requestDigest"`
	ExpiryTimestamp uint32   `json:"expiryTimestamp"`

	ChainSelector uint64                `json:"chainSelector"`
	MCMSConfig    *contracts.MCMSConfig `json:"mcmsConfig,omitempty"`
	Qualifier     string                `json:"qualifier"`
}

type UserAllowlistRequestOpOutput struct {
	Success         bool                      `json:"success"`
	RegistryAddress common.Address            `json:"registryAddress"`
	MCMSOperation   *mcmstypes.BatchOperation `json:"mcmsOperation"`
}

var UserAllowlistRequestOp = operations.NewOperation(
	"user-allowlist-request-op",
	semver.MustParse("1.0.0"),
	"User Allowlist Request in WorkflowRegistry V2",
	func(b operations.Bundle, deps WorkflowRegistryOpDeps, input UserAllowlistRequestOpInput) (UserAllowlistRequestOpOutput, error) {
		// Execute the transaction using the strategy
		operation, _, err := deps.Strategy.Apply(func(opts *bind.TransactOpts) (*types.Transaction, error) {
			tx, err := deps.Registry.AllowlistRequest(opts, input.RequestDigest, input.ExpiryTimestamp)
			if err != nil {
				return nil, fmt.Errorf("failed to call AllowlistRequest: %w", err)
			}
			return tx, nil
		})
		if err != nil {
			return UserAllowlistRequestOpOutput{}, fmt.Errorf("failed to execute AllowlistRequest: %w", err)
		}
		if operation != nil {
			deps.Env.Logger.Infof("Created MCMS proposal for AllowlistRequest on chain %d", input.ChainSelector)
		} else {
			deps.Env.Logger.Infof("Successfully user allowlisted request on chain %d", input.ChainSelector)
		}
		return UserAllowlistRequestOpOutput{
			Success:         true,
			MCMSOperation:   operation,
			RegistryAddress: deps.Registry.Address(),
		}, nil
	},
)
```

**File:** deployment/cre/workflow_registry/v2/changeset/user_workflow_registry.go (L716-736)
```go
// UserAllowlistRequest allows a user to request allowlist status
type UserAllowlistRequest struct{}

type UserAllowlistRequestInput struct {
	ExpiryTimestamp uint32 `json:"expiryTimestamp"`
	RequestDigest   string `json:"requestDigest"`

	ChainSelector             uint64                   `json:"chainSelector"`             // Chain Selector
	MCMSConfig                *crecontracts.MCMSConfig `json:"mcmsConfig,omitempty"`      // MCMS configuration
	WorkflowRegistryQualifier string                   `json:"workflowRegistryQualifier"` // Qualifier to identify the specific workflow registry
}

func (u UserAllowlistRequest) VerifyPreconditions(e cldf.Environment, config UserAllowlistRequestInput) error {
	if config.ExpiryTimestamp == 0 {
		return errors.New("expiry timestamp cannot be zero")
	}
	if len(config.RequestDigest) == 0 {
		return errors.New("request digest cannot be empty")
	}
	return nil
}
```
