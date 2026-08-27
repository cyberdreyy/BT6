Analyzing the code found, I found a genuine ordering flaw analogous to the H-4 bug class: in `AuthorizeRequest`, the replay guard permanently records the request digest as "seen" *before* the owner-binding validation runs, so a legitimate request whose owner-binding check fails still permanently consumes/burns the digest.### Title
Vault gateway `Authorizer.AuthorizeRequest` permanently burns the replay-guard digest before owner-binding validation, permanently locking out legitimate retries - ([File: core/capabilities/vault/authorizer.go])

### Summary
`Authorizer.AuthorizeRequest` records a request's digest in the replay guard (a one-time-use, irreversible action analogous to the H-4 token burn) *before* it validates that the payload's secret owners match the authorized workflow owner. If that later owner-binding check fails, the digest has already been permanently consumed, so a legitimate correction of the same logical request (same `request_id`/digest) can never be authorized again.

### Finding Description
`AuthorizeRequest` in `core/capabilities/vault/authorizer.go` performs the steps in this order:
1. Run the underlying auth mechanism (allowlist or JWT) to get an `AuthResult`.
2. Call `a.replayGuard.CheckAndRecord(authResult.Digest(), authResult.ExpiresAt())` — this is a one-way, stateful side effect that marks the digest as permanently "seen" for its expiry window.
3. Only *after* that, call `validateSecretOwnersMatchAuthorized(req, authResult.AuthorizedOwner())`. [1](#0-0) 

`RequestReplayGuard.CheckAndRecord` is a strict "record once" operation with no rollback path — once a digest is recorded it stays recorded until it expires, regardless of what happens afterward: [2](#0-1) 

Because the JSON-RPC request `Digest()` is computed over the whole request body (method, params, request ID) as used throughout the codebase (see replay tests keyed on `req.Digest()`), the digest for a request whose *body* fails owner validation is identical to the digest of a "corrected" resubmission with the *exact same body* (same request ID, same params) sent again — there is no way to retry the identical failed request once `CheckAndRecord` has consumed its digest. This is directly analogous to the `Redeemer.redeem` bug: an irreversible/one-shot resource (iPT tokens / replay-guard digest slot) is consumed unconditionally before the code confirms the operation will actually succeed and have the intended effect (transfer underlying / authorize a valid vault write).

Tests demonstrate the order dependency and that owner-mismatch errors occur *after* the auth mechanism (and therefore after replay recording) has already run: [3](#0-2) 

### Impact Explanation
An unprivileged (but otherwise correctly-allowlisted or JWT-authorized) workflow-owner client whose vault write/list request body happens to fail the owner-binding check (e.g. a benign client bug that mismatches `owner` inside `params` vs. the JWT/allowlist-derived owner, or a race where the allowlisted request's derived owner differs from the payload owner) will have its request's digest permanently burned by the replay guard on the very first, failing attempt. Any subsequent attempt to send the identical (now-corrected-elsewhere but same-digest) request is rejected with `ErrRequestAlreadySeen`, denying the legitimate operation for the life of the digest's expiry window (bounded by JWT/allowlist expiry). This can cause denial of legitimate vault-secret create/update/delete/list operations reachable directly from the internet-facing gateway pipeline (`core/services/gateway/handlers/vault/handler.go`, `GatewayVaultRequestProcessor.authorizeAndStamp`) — i.e., a self-inflicted but externally-triggerable permanent request lockout, not merely an in-place retry failure.

### Likelihood Explanation
Likelihood is low-to-moderate: it requires a client to submit a request whose auth mechanism succeeds (allowlisted or valid JWT) but whose payload owner field(s) don't match the authorized owner — a plausible client-side bug/misconfiguration given the pipeline explicitly documents "AuthorizeRequest runs while params are still digest-safe... validates that payload owners match the authorized workflow owner" as a distinct, later step. No malicious/privileged access is required; it is purely reachable via a standard gateway-routed vault JSON-RPC request from any client capable of forming an allowlisted or JWT-authorized request.

### Recommendation
Reorder the checks in `Authorizer.AuthorizeRequest` so that `validateSecretOwnersMatchAuthorized` runs *before* `replayGuard.CheckAndRecord`, ensuring the digest is only consumed once the request is fully authorized and known to be valid — mirroring the general principle from the analog report: don't perform an irreversible/one-shot action (burn a token, consume a replay-guard slot) until all preconditions for a successful effect have been confirmed.

### Proof of Concept
1. A client obtains a valid JWT/allowlist authorization for workflow owner `0xauthorized` and sends a `vault.secrets.create` request whose params contain a secret identifier `Owner: "0xother"` (a mismatch, e.g. due to a client bug).
2. `AuthorizeRequest` runs the allowlist/JWT check → succeeds → `AuthResult{workflowOwner: "0xauthorized", digest: D}`.
3. `replayGuard.CheckAndRecord(D, expiry)` succeeds and records `D` as seen.
4. `validateSecretOwnersMatchAuthorized` fails with `"encrypted secret owner at index 0 \"0xother\" does not match authorized workflow owner \"0xauthorized\""`, and the whole request is rejected — as shown in [3](#0-2) .
5. The client retries the identical request (same ID/params, thus same digest `D`) after fixing nothing else (or fixing an unrelated aspect that doesn't change the digest) — `replayGuard.CheckAndRecord(D, ...)` now returns `ErrRequestAlreadySeen`, permanently blocking this request even though it was never actually authorized/executed successfully.

### Citations

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

**File:** core/capabilities/vault/authorizer_test.go (L148-170)
```go
func TestAuthorizer_AllowListPath_RejectsCreateOwnerMismatch(t *testing.T) {
	params, err := json.Marshal(vaultcommon.CreateSecretsRequest{
		EncryptedSecrets: []*vaultcommon.EncryptedSecret{
			{Id: &vaultcommon.SecretIdentifier{Owner: "0xother", Namespace: "ns", Key: "k"}, EncryptedValue: "cipher"},
		},
	})
	require.NoError(t, err)

	req := jsonrpc.Request[json.RawMessage]{
		ID:     "1",
		Method: vaulttypes.MethodSecretsCreate,
		Params: (*json.RawMessage)(&params),
	}

	allowListBasedAuth := vaultmocks.NewAuthorizer(t)
	allowListBasedAuth.EXPECT().AuthorizeRequest(mock.Anything, req).Return(vault.NewAuthResult("", "0xauthorized", "digest-1", time.Now().Add(time.Minute).Unix()), nil).Once()

	a := vault.NewAuthorizer(allowListBasedAuth, nil, logger.TestLogger(t))

	authResult, err := a.AuthorizeRequest(t.Context(), req)
	require.Nil(t, authResult)
	require.ErrorContains(t, err, "encrypted secret owner at index 0 \"0xother\" does not match authorized workflow owner \"0xauthorized\"")
}
```
