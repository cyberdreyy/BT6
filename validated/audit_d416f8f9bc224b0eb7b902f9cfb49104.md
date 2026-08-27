### Title
`RequestReplayGuard` is process-local, allowing signed Vault requests to be replayed across horizontally-scaled gateway instances - ([File: core/capabilities/vault/request_replay_guard.go])

### Summary
`RequestReplayGuard` stores seen request digests only in an in-process `map[string]int64` guarded by a `sync.Mutex`, with no external/shared persistence. Since `vault.NewAuthorizer` (and therefore `NewRequestReplayGuard()`) is instantiated once per `handler` created by `NewHandler`/`newHandlerWithAuthorizer` in `core/services/gateway/handlers/vault/handler.go`, every gateway process/replica servicing a DON gets its own independent guard, so a signed request accepted and recorded on one replica is unknown to another.

### Finding Description
`AuthorizeRequest` in `core/capabilities/vault/authorizer.go` computes an `AuthResult` (owner, digest, expiry) via the allow-list or JWT auth path and then calls `a.replayGuard.CheckAndRecord(authResult.Digest(), authResult.ExpiresAt())` [1](#0-0)  to reject duplicates. The guard itself is a plain in-memory map with no shared backing store: [2](#0-1) 

Each `vault.handler` (one per DON/gateway node, created via `NewHandler`) builds its own `Authorizer` — and hence its own `RequestReplayGuard` — locally: [3](#0-2) 

`HandleJSONRPCUserMessage` routes `MethodSecretsList` (and other vault methods) through `h.requestProcessor.ProcessRequest`, which ultimately invokes this per-instance authorizer/guard: [4](#0-3) 

Because the guard's `seen` map exists only in the memory of the specific gateway process that first handled the request, any deployment that runs more than one instance of the same gateway/DON endpoint (for HA, load-balancing, or blue/green rollout) — a topology explicitly supported by the codebase's multi-gateway configs [5](#0-4)  — will not share replay state across instances. A legitimate, validly-signed request (e.g., `MethodSecretsList`) captured/retained by its own low-privileged sender can be resubmitted and will be independently authorized and processed by each distinct instance, because there is no cross-instance digest coordination (no shared cache/DB/Redis) anywhere in the authorizer/replay-guard implementation.

### Impact Explanation
This breaks the invariant that a signed request's identity/authorization cannot be replayed once consumed. For `MethodSecretsList` the direct scoped impact is duplicate processing/quota consumption of a read-only operation against the requester's own data; more broadly the same code path underlies all `vaulttypes` methods (including write methods `SecretsCreate/Update/Delete`), so on a horizontally-scaled deployment the same signed write request could similarly be double-processed across replicas, causing duplicate node fan-out, wasted quorum/aggregation cycles, and potential allow-list/quota-limit bypass (`writeMethodsEnabled` rate limiting is also per-instance). This corresponds to Chainlink's "allowlist/quota bypass" bounty impact class rather than a full authentication bypass, since the replay is still gated by needing a still-unexpired, self-owned, validly-signed request.

### Likelihood Explanation
Exploitability requires only: (1) a legitimate, low-privileged actor's own valid signed vault request (no other credentials needed — the attacker is simply replaying their own captured envelope), and (2) the operator's gateway/DON endpoint being deployed with more than one instance/replica handling the same DON traffic without a shared cache. The vulnerability is deterministic and trivially reproducible in a distributed/HA deployment before the digest expiry window elapses (`authResult.ExpiresAt()`), and every additional replica linearly increases the replay window.

### Recommendation
Back `RequestReplayGuard` with a shared, cross-instance store (e.g., Redis, or the existing Postgres DB) keyed by digest with TTL equal to `expiresAtUnix`, so `CheckAndRecord` performs an atomic check-and-set visible to all gateway replicas for a given DON. Alternatively, document and enforce (at the deployment/orchestration layer) that each DON's vault-handling gateway must run as a single logical instance, or require sticky routing keyed on request digest so replays always land on the same instance within the guard being introduced as a first defense.

### Proof of Concept
```go
func TestRequestReplayGuard_CrossInstanceReplay(t *testing.T) {
    // Simulate two independent gateway replicas, each with its own authorizer
    // (mirrors NewHandler -> vaultcap.NewAuthorizer wiring, one per process).
    allowList := newTestAllowListAuth(t) // stub returning same AuthResult for both calls
    authorizerA := vaultcap.NewAuthorizer(allowList, nil, lggr)
    authorizerB := vaultcap.NewAuthorizer(allowList, nil, lggr)

    signedReq := buildSignedSecretsListRequest(t, ownerAddr) // same jsonrpc.Request[json.RawMessage] both times

    resA, errA := authorizerA.AuthorizeRequest(ctx, signedReq)
    require.NoError(t, errA)
    require.NotNil(t, resA)

    // Same envelope replayed against a *different* instance's authorizer/guard.
    resB, errB := authorizerB.AuthorizeRequest(ctx, signedReq)
    require.NoError(t, errB) // FAILS the invariant: expected ErrRequestAlreadySeen
    require.NotNil(t, resB)
}
```
Expected (buggy) result: both `authorizerA` and `authorizerB` return a non-nil `AuthResult` with no error, demonstrating that the digest recorded by instance A's `RequestReplayGuard` is invisible to instance B, allowing the same signed `MethodSecretsList` request to be accepted twice across gateway replicas.

### Citations

**File:** core/capabilities/vault/authorizer.go (L109-112)
```go
	if err := a.replayGuard.CheckAndRecord(authResult.Digest(), authResult.ExpiresAt()); err != nil {
		a.lggr.Debugw("replay guard rejected request", "method", req.Method, "requestID", req.ID, "owner", authResult.AuthorizedOwner(), "digest", authResult.Digest(), "expiresAt", authResult.ExpiresAt(), "hasAuth", req.Auth != "", "error", err)
		return nil, err
	}
```

**File:** core/capabilities/vault/request_replay_guard.go (L16-28)
```go
type RequestReplayGuard struct {
	mu      sync.Mutex
	seen    map[string]int64 // digest → unix expiry timestamp
	nowFunc func() time.Time // injectable for testing
}

// NewRequestReplayGuard creates a replay guard for authorized Vault requests.
func NewRequestReplayGuard() *RequestReplayGuard {
	return &RequestReplayGuard{
		seen:    make(map[string]int64),
		nowFunc: time.Now,
	}
}
```

**File:** core/services/gateway/handlers/vault/handler.go (L192-209)
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

	return newHandlerWithAuthorizer(methodConfig, donConfig, don, capabilitiesRegistry, authorizer, jwtAuth, lggr, clock, limitsFactory)
```

**File:** core/services/gateway/handlers/vault/handler.go (L431-446)
```go
	if !vaulttypes.IsGatewaySecretsMethod(req.Method) {
		return h.sendImmediateUserResponse(ctx, req, callback, api.UnsupportedMethodError, errors.New("this method is unsupported: "+req.Method))
	}

	_, cachedPublicKey := h.getCachedPublicKey()
	authorized, err := h.requestProcessor.ProcessRequest(ctx, &req, cachedPublicKey)
	if err != nil {
		if vaultcap.IsInvalidVaultParamsError(err) {
			return h.sendImmediateUserResponse(ctx, req, callback, api.InvalidParamsError, err)
		}
		h.lggr.Errorw("request not authorized", "method", req.Method, "requestID", req.ID, "hasAuth", req.Auth != "", "error", err)
		return errors.New("request not authorized: " + err.Error())
	}
	authorizedOwner := authorized.AuthResult.AuthorizedOwner()

	h.lggr.Debugw("handling authorized vault request", "method", req.Method, "requestID", req.ID, "authorizedOwner", authorizedOwner)
```

**File:** core/scripts/cre/environment/configs/workflow-gateway-capabilities-multi-gateway-don.toml (L32-42)
```text
[[nodesets]]
  nodes = 4
  name = "workflow"
  don_family = "test-don-family"
  don_types = ["workflow"]
  override_mode = "all"
  http_port_range_start = 10100
  supported_evm_chains = [1337, 2337]
  env_vars = { CL_EVM_CMD = "", OTEL_SERVICE_NAME = "chainlink-node", CL_CRE_SETTINGS = '{"global":{"PropagateOrgIDInRequestMetadata":"true","PerOrg":{"BaseTriggerRetransmitEnabled":"true"}},"org":{"multi-don-test-org":{"PerWorkflow":{"HTTPAction":{"GatewayProxyDonID":"gateway_don_eu"}}}}}' }
  capabilities = ["cron", "http-action", "http-trigger", "consensus", "don-time", "evm-1337"]
  registry_based_launch_allowlist = ["cron-trigger@1.0.0"]
```
