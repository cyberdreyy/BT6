### Title
Unauthenticated replay/impersonation of vault requests via digest-only `AllowListBasedAuth` lets an untrusted actor deny or hijack a legitimately allowlisted request - (File: core/capabilities/vault/allow_list_based_auth.go, core/capabilities/vault/authorizer.go, core/capabilities/vault/request_replay_guard.go)

### Summary
`AllowListBasedAuth.AuthorizeRequest` authorizes a Vault gateway request purely by matching a content digest against an on-chain allowlist entry — it never verifies that the caller is the owner who registered that entry, nor does it bind the digest-consuming replay guard to the caller's identity. Any untrusted client that can reproduce the exact bytes of a pre-registered (and therefore publicly observable, on-chain) request can (a) get the request authorized as if it came from the legitimate workflow owner, and (b) permanently consume the shared `RequestReplayGuard` entry, causing the real owner's subsequent submission of the same request to be rejected as `ErrRequestAlreadySeen`. This mirrors the reported bug class: an unprivileged actor can "slash"/invalidate a valid message with no access control gating the state-mutating check.

### Finding Description
`allowListBasedAuth.AuthorizeRequest` computes a digest of the incoming JSON-RPC request and checks it against digests published on-chain by the `WorkflowRegistrySyncer` (`GetAllowlistedRequests`): [1](#0-0) 

The only thing checked is digest equality — `fetchAllowlistedItem` iterates the allowlisted entries and returns a match on `item.RequestDigest == digest`, with no check that the HTTP/gateway caller who sent the request is cryptographically tied to that digest (no signature, no session binding, no owner-derived MAC): [2](#0-1) 

`authorizer.AuthorizeRequest` then records that digest in a single, shared, un-scoped `RequestReplayGuard` keyed only by the digest string, without any binding to the requester: [3](#0-2) [4](#0-3) 

Because the allowlisted `RequestDigest` values come from an on-chain `WorkflowRegistry` contract (public state, readable by anyone via `GetAllowlistedRequests`), and the underlying request content for `MethodSecretsDelete`/`MethodSecretsList` (owner address, namespace, key name, request ID) contains no secret/random material, an attacker who can guess or observe that plaintext (e.g., well-known/default key names, a previously-seen namespace, or leaked request metadata) can reconstruct byte-identical JSON-RPC request params that hash to the same on-chain digest. Submitting that reconstructed request through the public gateway (`core/services/gateway/gateway.go` `ProcessRequest` → `GatewayHandler.HandleGatewayMessage`) will: [5](#0-4) 

1. Pass `AllowListBasedAuth` (digest matches),
2. Consume the replay-guard slot for that digest before the real owner submits it, and
3. Actually execute the privileged action (`processDeleteSecretsRequest`/`processListSecretIdentifiersRequest`) since the processor derives authorization purely from `authResult.AuthorizedOwner()` taken from the matched allowlist entry, not from any signature proving the caller is that owner: [6](#0-5) 

The result is either (a) denial-of-service: the genuine owner's later, identical, legitimately-authorized request is rejected with `ErrRequestAlreadySeen` ("slashing" a valid message), or (b) impersonation: the attacker's forged request is processed as if it were the owner's, causing an unauthorized `DeleteSecrets`/`ListSecretIdentifiers` operation to execute under the owner's identity.

### Impact Explanation
This allows an unprivileged, unauthenticated gateway client to:
- Deny service to a legitimate workflow owner by pre-consuming the digest-keyed replay guard for a request the owner has not yet submitted (analogous to the reported "slash VALID messages" bug class — griefing/DoS on valid, pre-authorized state).
- In the worst case, cause an unauthorized state-changing vault operation (secret deletion, secret listing) to be executed under another user's authorized identity without holding any owner credentials, purely by reconstructing publicly-derivable request bytes.

This is High-severity in the sense that it breaks the core security invariant of the allowlist ("only the owner who registered this digest can have it executed"), though exploitability depends on the attacker being able to reconstruct the exact plaintext request content that hashes to a digest they've observed on-chain.

### Likelihood Explanation
Likelihood is Medium: it requires the attacker to reconstruct plaintext request params (owner address, namespace, key, request ID) that hash to a digest already visible on-chain via `WorkflowRegistrySyncer`. For `MethodSecretsCreate`/`MethodSecretsUpdate`, the `EncryptedSecrets` ciphertext is generated with per-call randomness, making digest collision practically infeasible. For `MethodSecretsDelete`/`MethodSecretsList`, params contain no secret entropy, so if key/namespace naming conventions are predictable (which they often are in workflow tooling, e.g., default namespace + well-known secret key names), reconstructing the exact digest match is realistic.

### Recommendation
Bind authorization to caller identity rather than digest-only content matching:
- Require the gateway request to carry a cryptographic proof (signature) from the workflow owner's key over the request digest, and verify it in `AllowListBasedAuth.AuthorizeRequest`, instead of accepting any caller who can reproduce the raw bytes.
- Scope the `RequestReplayGuard` per authorized owner (not a single global digest map), so an unrelated party cannot pre-empt or consume another owner's allowlist slot.
- For `MethodSecretsDelete`/`MethodSecretsList`, ensure the on-chain allowlist commitment includes enough entropy (e.g., a nonce bound to a signature) so the digest cannot be reconstructed by a third party from guessable plaintext alone.

### Proof of Concept
1. Owner `O` allowlists (on-chain, via `WorkflowRegistry`) a `MethodSecretsDelete` request with `RequestDigest = H(method, id, {owner:O, namespace:"main", key:"API_KEY"})` and some `ExpiryTimestamp`.
2. Attacker `A` (no credentials) reads `GetAllowlistedRequests` from the public `WorkflowRegistrySyncer` and observes the digest is registered for owner `O`, but the digest alone does not reveal params.
3. `A` guesses/knows that `O` typically uses `namespace:"main"`, `key:"API_KEY"`, and a predictable request `id` (e.g., sequential or fixed by tooling convention), and submits a `DeleteSecrets` JSON-RPC request to the public gateway with exactly those params before `O` submits the real one.
4. `allowListBasedAuth.AuthorizeRequest` computes the same digest from `A`'s request, finds the match in the on-chain allowlist, and returns `AuthResult{workflowOwner: O}` — despite `A` never proving ownership of `O`'s key.
5. `authorizer.AuthorizeRequest` records the digest in `RequestReplayGuard`, and the delete is executed by `GatewayHandler.handleSecretsDelete` for owner `O`'s secret.
6. When the legitimate owner `O` later submits the identical request, `RequestReplayGuard.CheckAndRecord` returns `ErrRequestAlreadySeen`, and `O`'s valid, properly-authorized request is rejected — the "slashing" of a valid message described in the source report's bug class.

### Citations

**File:** core/capabilities/vault/allow_list_based_auth.go (L32-62)
```go
// AuthorizeRequest authorizes a request using AllowListBasedAuth.
// It does NOT check if the request method is allowed.
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

**File:** core/capabilities/vault/authorizer.go (L99-119)
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
	if ownerErr := validateSecretOwnersMatchAuthorized(req, authResult.AuthorizedOwner()); ownerErr != nil {
		a.lggr.Errorw("owner binding rejected request", "method", req.Method, "requestID", req.ID, "owner", authResult.AuthorizedOwner(), "hasAuth", req.Auth != "", "error", ownerErr)
		return nil, ownerErr
	}
	a.lggr.Debugw("request authorized", "method", req.Method, "requestID", req.ID, "owner", authResult.AuthorizedOwner(), "digest", authResult.Digest(), "expiresAt", authResult.ExpiresAt(), "hasAuth", req.Auth != "")
	return authResult, nil
}
```

**File:** core/capabilities/vault/request_replay_guard.go (L35-47)
```go
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

**File:** core/capabilities/vault/gw_handler.go (L180-211)
```go
func (h *GatewayHandler) HandleGatewayMessage(ctx context.Context, gatewayID string, req *jsonrpc.Request[json.RawMessage]) (err error) {
	reqLggr := h.requestLogger(req, gatewayID)
	reqLggr.Debugw("received message from gateway", "req", req)

	var response *jsonrpc.Response[json.RawMessage]
	var authResult *AuthResult

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

**File:** core/capabilities/vault/gateway_vault_request_processor.go (L222-255)
```go
func (p *GatewayVaultRequestProcessor) authorizeAndStamp(
	ctx context.Context,
	req *jsonrpc.Request[json.RawMessage],
	stamp func(prefixedRequestID string) error,
) (*AuthorizedGatewayVaultRequest, error) {
	incomingOwner := ""
	if idx := strings.Index(req.ID, vaulttypes.RequestIDSeparator); idx != -1 {
		incomingOwner = req.ID[:idx]
	}

	p.lggr.Debugw("authorizing gateway vault request", "method", req.Method, "requestID", req.ID)
	authResult, err := p.authorizer.AuthorizeRequest(ctx, *req)
	if err != nil {
		authErr := fmt.Errorf("request not authorized: %w", err)
		p.lggr.Errorw("gateway vault request authorization failed", "method", req.Method, "requestID", req.ID, "hasAuth", req.Auth != "", "incomingOwner", incomingOwner, "error", authErr)
		return nil, authErr
	}

	originalRequestID := req.ID
	authorizedOwner := authResult.AuthorizedOwner()
	prefixedRequestID := authorizedOwner + vaulttypes.RequestIDSeparator + originalRequestID
	req.ID = prefixedRequestID

	if err := stamp(prefixedRequestID); err != nil {
		p.lggr.Errorw("failed to stamp authorized request params", "method", req.Method, "requestID", req.ID, "error", err)
		return nil, fmt.Errorf("failed to stamp authorized request params: %w", err)
	}

	p.lggr.Debugw("authorized gateway vault request", "method", req.Method, "requestID", req.ID, "owner", authorizedOwner, "orgID", authResult.OrgID(), "workflowOwner", authResult.WorkflowOwner())
	return &AuthorizedGatewayVaultRequest{
		Req:        *req,
		AuthResult: authResult,
	}, nil
}
```
