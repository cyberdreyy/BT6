### Title
Vault allowlist-based auth authorizes any submitter of a matching request digest, allowing race-based nonce consumption / request front-running - ([File: core/capabilities/vault/allow_list_based_auth.go])

### Summary
The Vault's `allowListBasedAuth.AuthorizeRequest` [1](#0-0)  and the shared `authorizer.AuthorizeRequest` replay-guard check [2](#0-1)  authorize a request purely based on whether its content-derived `Digest()` matches an on-chain allowlisted entry (`WorkflowRegistryOwnerAllowlistedRequest.RequestDigest`), and consume a single-use, process-global replay slot keyed only by that digest. Nothing in this path binds the authorization to the specific caller/session that submits the HTTP/JSON-RPC request to the gateway — analogous to the Kyber hooks that validate `sender == router` (a shared, public identity) without binding the signed swap to the specific end user who owns it.

### Finding Description
`allowListBasedAuth.AuthorizeRequest` computes `req.Digest()` from the request's method and params only [3](#0-2) , and checks whether that digest was previously registered on-chain via `WorkflowRegistry.AllowlistRequest(requestDigest, expiryTimestamp)` [4](#0-3) . If found and unexpired, it returns an `AuthResult` bound to the `Owner` recorded on-chain — but it never verifies who is actually submitting the HTTP request to the gateway [5](#0-4) .

The generic `authorizer.AuthorizeRequest` then applies a single global replay guard keyed by that same digest: `a.replayGuard.CheckAndRecord(authResult.Digest(), authResult.ExpiresAt())` [6](#0-5) . This is explicitly a first-submission-wins, one-shot consumption mechanism (confirmed by the test comment "Same request is still authorized here; replay protection lives in the generic Authorizer" in the allow-list auth unit test, meaning the allow-list check itself does not prevent replays, only the shared digest-keyed guard does) [7](#0-6) .

This mirrors the reported bug class precisely: the check validates *what* (content/digest matches an authorized "quote") but not *who* is presenting it. Just as the Kyber hooks check `sender == router` (a shared, non-user-specific identity) instead of binding to the individual trader, Chainlink's allowlist-auth binds authorization to a content digest instead of to the specific requester's session/identity. Any party capable of reproducing or intercepting the exact request bytes that hash to an allowlisted digest (e.g., a component in the request path — client-side proxy, load balancer, logging middleware, or a duplicate/retried submission from the legitimate client itself) can submit it to the gateway ahead of the intended caller and consume the one-time replay slot. The legitimate caller's subsequent (identical) submission is then rejected with `ErrRequestAlreadySeen`, exactly matching the described "attacker consumes the nonce, the legitimate transaction reverts on `_useUnorderedNonce`" pattern.

### Impact Explanation
A raced/duplicated submission of an allowlisted Vault request (secrets create/update/delete, or list) can be consumed by an unrelated actor with access to the exact wire-format request before the legitimate owner-authorized caller's request lands, causing the legitimate caller's request to be denied ("request already seen" / replay rejection) even though it was properly authorized on-chain by the owner. This is a denial-of-service / request-griefing vector on privileged (owner-approved) Vault operations rather than a full identity spoof, since the allowlist entry still requires the operation-owner match check (`validateSecretOwnersMatchAuthorized`) [8](#0-7) . It does not directly leak secrets or bypass the owner check, but it can block or "frontrun" legitimate secret create/update/delete operations that were pre-approved via an on-chain allowlist entry.

### Likelihood Explanation
Exploitation requires an attacker to obtain the exact JSON-RPC request bytes (method + params) before the legitimate submission completes — e.g., via network-level interception, a shared/misconfigured proxy, gateway-side logging, or the legitimate client itself retrying a request under contention. This is a narrower window than the Kyber mempool-frontrunning case (where calldata/signature are trivially public in the mempool), since the Vault allowlist digest alone (which is public on-chain) is not sufficient to reconstruct the full request without already knowing its content. Likelihood is therefore lower than the reported analog but the root-cause pattern — authorizing by content digest without binding the specific submitter — is structurally identical.

### Recommendation
Bind authorization to the specific requester/session in addition to the content digest — e.g., require a per-caller nonce or session identifier as part of what is hashed/allowlisted, or require the allowlist-based flow to carry additional caller-identity binding (similar to how the JWT-based path binds `authorization_details.request_digest` to a specific signed bearer token tied to an authenticated principal, see `jwt_based_auth_test.go`) [9](#0-8) . At minimum, make the replay guard fail closed in favor of the legitimate submitter by tying redemption to a client-specific correlation (e.g., request source/session) rather than purely a global digest.

### Proof of Concept
1. Owner allowlists a Vault request by posting `AllowlistRequest(requestDigest, expiry)` on-chain for a specific `CreateSecretsRequest` payload [4](#0-3) .
2. The legitimate client prepares to submit the exact JSON-RPC request (method + params) whose `Digest()` matches the allowlisted entry.
3. An intercepting party (e.g., anything positioned to observe the exact request bytes before they reach the gateway node) submits the identical request to the gateway first.
4. `allowListBasedAuth.AuthorizeRequest` succeeds because the digest matches the on-chain allowlist entry [10](#0-9) , and `replayGuard.CheckAndRecord` records the digest as consumed [6](#0-5) .
5. When the legitimate client's original submission arrives, `AuthorizeRequest` is called again with the same digest and fails with `ErrRequestAlreadySeen`, denying the legitimate, owner-authorized request — the analog of the "attacker consumes the nonce" step in the Kyber report.

### Citations

**File:** core/capabilities/vault/allow_list_based_auth.go (L34-77)
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
	}

	if time.Now().UTC().Unix() > int64(allowlistedRequest.ExpiryTimestamp) {
		authorizedRequestStr := string(allowlistedRequest.RequestDigest[:])
		r.lggr.Debugw("AllowListBasedAuth authorization expired", "method", req.Method, "requestID", req.ID, "authorizedRequestStr", authorizedRequestStr, "expiryTimestamp", allowlistedRequest.ExpiryTimestamp)
		return nil, errors.New("request authorization expired")
	}

	digestKey := string(allowlistedRequest.RequestDigest[:])
	r.lggr.Debugw("AllowListBasedAuth authorization succeeded", "method", req.Method, "requestID", req.ID, "authorizedRequestStr", digestKey, "owner", allowlistedRequest.Owner.Hex(), "expiryTimestamp", allowlistedRequest.ExpiryTimestamp)
	return &AuthResult{
		workflowOwner: allowlistedRequest.Owner.Hex(),
		digest:        digestKey,
		expiresAt:     int64(allowlistedRequest.ExpiryTimestamp),
	}, nil
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

**File:** core/services/workflows/syncer/v2/workflow_syncer_v2_test.go (L881-904)
```go
func allowlistRequest(
	t *testing.T,
	th *testutils.EVMBackendTH,
	wfRegC *workflow_registry_wrapper_v2.WorkflowRegistry,
	input allowlistRequestParams,
) {
	t.Helper()
	totalAllowlistedRequestsBefore, err := wfRegC.TotalAllowlistedRequests(&bind.CallOpts{
		From: th.ContractsOwner.From,
	})
	require.NoError(t, err, "failed to get total allowlisted requests")

	requestDigest, err := input.Request.Digest()
	require.NoError(t, err)
	requestDigestBytes, err := hex.DecodeString(requestDigest)
	require.NoError(t, err)

	_, err = wfRegC.AllowlistRequest(
		th.ContractsOwner,
		[32]byte(requestDigestBytes),
		uint32(input.ExpiryTimestamp.Unix()), //nolint:gosec // safe conversion
	)
	require.NoError(t, err, "failed to register allowlisted request")
	th.Backend.Commit()
```

**File:** core/capabilities/vault/allow_list_based_auth_test.go (L186-189)
```go
	// Same request is still authorized here; replay protection lives in the generic Authorizer.
	authResult, err = auth.AuthorizeRequest(t.Context(), allowlistedRequest)
	require.NoError(t, err)
	require.Equal(t, owner.Hex(), authResult.AuthorizedOwner())
```

**File:** core/capabilities/vault/jwt_based_auth_test.go (L911-926)
```go
	token := createTestJWT(t, rsaKey, jwt.MapClaims{
		"iss":                             issuer,
		"aud":                             audience,
		"exp":                             jwt.NewNumericDate(time.Now().Add(5 * time.Minute)),
		"iat":                             jwt.NewNumericDate(time.Now()),
		"org_id":                          "org-123",
		ClaimVaultSecretManagementEnabled: "true",
		ClaimChainlinkTenantID:            "1",
		"permissions":                     []any{OAuthScopeVaultSecretsList},
		"authorization_details": []any{
			map[string]any{
				"type":  "request_digest",
				"value": digest,
			},
		},
	})
```
