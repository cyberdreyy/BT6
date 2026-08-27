## Root Cause

The Vault gateway's replay-protection mechanism tracks used request digests in a single collective map that is not scoped per authenticated caller/owner — the same anti-pattern flagged in the external report about `usedNonces` not being tracked per address.

`RequestReplayGuard` stores `seen map[string]int64` keyed only by `digest`, with no notion of which workflow owner/org produced the request: [1](#0-0) 

The generic `Authorizer.AuthorizeRequest` calls this guard **after** delegating to either the allowlist-based or JWT-based auth flow, using only the request digest — never combining it with the resolved owner/org identity: [2](#0-1) 

The digest itself, `req.Digest()`, is computed purely from the JSON-RPC `method`, `id`, and `params` fields of the request — it does not necessarily bind to the caller's identity unless the caller's owner address happens to be embedded inside the params for that particular method: [3](#0-2) 

For example, `MethodSecretsList` requests can be built with only a `Namespace` (no `Owner`) field, as shown in the allowlist auth test: [4](#0-3) 

### Title
Global (non-owner-scoped) replay-digest tracking in Vault Authorizer causes cross-user denial of service - (File: core/capabilities/vault/request_replay_guard.go)

### Summary
`RequestReplayGuard` deduplicates authorized Vault requests using a single shared map keyed solely by the request digest (hash of `method`+`id`+`params`), the same "collective nonce tracking" flaw described in the external report. Because the digest is not combined with the caller's authorized owner/org identity before being recorded, two unrelated, independently-authorized users who happen to submit a request with the same `method`, client-chosen `id`, and `params` (e.g. both list secrets in the same default namespace using `id: "1"`) will collide on the same digest. Whichever request is processed second is rejected with `ErrRequestAlreadySeen`, denying service to a legitimate, distinct user.

### Finding Description
`Authorizer.AuthorizeRequest` first resolves the caller's identity/owner via `authorizeAllowListBasedAuth` or `authorizeJWTBasedAuth`, then calls `a.replayGuard.CheckAndRecord(authResult.Digest(), authResult.ExpiresAt())` using only the digest — the owner is never mixed into the replay key: [5](#0-4) 

`CheckAndRecord` rejects any digest that has already been seen from *any* caller, regardless of who authorized it: [6](#0-5) 

For the allowlist-based flow, each owner independently gets requests allowlisted on-chain and the digest is derived purely from `method`+`id`+`params` of the client request (test flow using `ListSecretIdentifiersRequest{Namespace: "b"}` with no `Owner` field set demonstrates the digest does not have to include the requester's identity): [7](#0-6) 

Thus if User A and User B are both independently allowlisted (or JWT-authorized) to make a structurally identical request — same method, same client-supplied `id`, same params — their digests are identical strings. The first request to reach `CheckAndRecord` "consumes" that shared digest, and the second, legitimate, distinct request from an unrelated user is rejected with `ErrRequestAlreadySeen`, exactly mirroring the underlying bug class in the report (nonce/nonce-like state tracked globally instead of per-identity).

### Impact Explanation
This is a denial-of-service vector against unrelated, legitimately-authorized Vault users. An attacker who can predict or brute-force common request shapes (fixed `id` values and common `params`, e.g. default namespace listings) used by other workflow owners can pre-empt them by submitting a colliding request first, causing the victim's genuine request to be rejected. It also causes non-malicious accidental collisions (e.g., two workflows both using `id: "1"` for their first request) to spuriously fail, which is itself a reliability/availability bug matching the "unexpected and arbitrary denial of service" impact called out in the source report.

### Likelihood Explanation
Likelihood is moderate: it requires the attacker (or coincidence) to produce a request with an identical `method` + `id` + `params` combination to a genuine different-owner request. Because `id` is client-chosen and often follows predictable patterns (sequential integers, UUIDs reused across retries, etc.), and because not all Vault methods embed the caller's identity in `params` (e.g., `MethodSecretsList`/`MethodSecretsDelete` with only namespace/keys), collisions are plausible without needing to compromise any keys or bypass authentication — the attacker only needs their own valid (unrelated) authorization to submit a request.

### Recommendation
Scope the replay guard's key to `(authorizedOwner/orgID, digest)` instead of `digest` alone, so that identical request shapes from different authorized principals cannot collide. This mirrors the fix pattern in the report: track used identifiers per authenticated principal, not as one flat collective structure. Concretely, in `authorizer.go`, derive the guard key from `authResult.AuthorizedOwner()` (or `OrgID()`) combined with `authResult.Digest()` before calling `CheckAndRecord`.

### Proof of Concept
1. Owner A gets a `vault.secrets.list` request with `id: "1"`, `params: {"namespace":"default"}` allowlisted (or JWT-authorized) for their address.
2. Owner B, an entirely separate, legitimately allowlisted/JWT-authorized user, independently constructs a request with the exact same `method`, `id: "1"`, and `params: {"namespace":"default"}`.
3. Owner A's request is processed first: `AuthorizeRequest` succeeds, and `RequestReplayGuard.CheckAndRecord(digest)` records the digest (see test flow at `core/capabilities/vault/gw_handler_test.go:682-737`, which demonstrates the same-digest-rejects-second-request behavior, but here triggered by a *different, unrelated* owner rather than a true replay).
4. Owner B's structurally-identical request is now rejected with `ErrRequestAlreadySeen`/`"request was already authorized previously"` even though Owner B never resent a request and has independent, valid authorization — a denial of service against Owner B caused solely by Owner A's unrelated traffic.

### Citations

**File:** core/capabilities/vault/request_replay_guard.go (L16-47)
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

// CheckAndRecord returns ErrRequestAlreadySeen if the digest was previously
// recorded and has not yet expired. Otherwise it records the digest with
// the given expiry timestamp (unix seconds, UTC).
//
// Expired entries are cleaned up on every call.
func (g *RequestReplayGuard) CheckAndRecord(digest string, expiresAtUnix int64) error {
	g.mu.Lock()
	defer g.mu.Unlock()

	g.clearExpiredLocked()

	if _, exists := g.seen[digest]; exists {
		return ErrRequestAlreadySeen
	}

	g.seen[digest] = expiresAtUnix
	return nil
}
```

**File:** core/capabilities/vault/authorizer.go (L99-112)
```go
func (a *authorizer) AuthorizeRequest(ctx context.Context, req jsonrpc.Request[json.RawMessage]) (*AuthResult, error) {
	authResult, err := a.authorizeRequest(ctx, req)
	if err != nil {
		return nil, err
	}
	if authResult == nil {
		err = errors.New("auth mechanism returned nil auth result")
		a.lggr.Errorw("auth mechanism returned nil auth result", "method", req.Method, "requestID", req.ID, "hasAuth", req.Auth != "")
		return nil, err
	}
	if err := a.replayGuard.CheckAndRecord(authResult.Digest(), authResult.ExpiresAt()); err != nil {
		a.lggr.Debugw("replay guard rejected request", "method", req.Method, "requestID", req.ID, "owner", authResult.AuthorizedOwner(), "digest", authResult.Digest(), "expiresAt", authResult.ExpiresAt(), "hasAuth", req.Auth != "", "error", err)
		return nil, err
	}
```

**File:** core/capabilities/vault/allow_list_based_auth_test.go (L134-155)
```go
func TestAllowListBasedAuth_ListSecrets(t *testing.T) {
	params, err := json.Marshal(vaultcommon.ListSecretIdentifiersRequest{
		Namespace: "b",
	})
	allowListedReq := jsonrpc.Request[json.RawMessage]{
		ID:     "123",
		Method: vaulttypes.MethodSecretsList,
		Params: (*json.RawMessage)(&params),
	}
	require.NoError(t, err)
	notAllowedParams, err := json.Marshal(vaultcommon.ListSecretIdentifiersRequest{
		Namespace: "not allowed",
	})
	require.NoError(t, err)
	notAllowListedReq := jsonrpc.Request[json.RawMessage]{
		ID:     "123",
		Method: vaulttypes.MethodSecretsList,
		Params: (*json.RawMessage)(&notAllowedParams),
	}
	require.NoError(t, err)
	testAuthForRequests(t, allowListedReq, notAllowListedReq)
}
```
