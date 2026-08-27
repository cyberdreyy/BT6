### Title
Vault gateway request replay guard is consumed on authorization, not on successful execution, permanently bricking a legitimate secrets create/update/delete/list request if downstream processing fails - ([File: core/capabilities/vault/authorizer.go])

### Summary
The Vault gateway request pipeline (`GatewayHandler.HandleGatewayMessage` → `GatewayVaultRequestProcessor.ProcessRequest` → `authorizer.AuthorizeRequest`) records a request's digest in the `RequestReplayGuard` at authorization time, *before* the actual secrets operation (`CreateSecrets`/`UpdateSecrets`/`DeleteSecrets`/`ListSecretIdentifiers`) is executed. If the downstream secrets-service call fails for any transient or environmental reason after the digest has been consumed, the same request can never be retried successfully until its allowlist/JWT expiry window passes — the resource (a single-use allowlisted or JWT-scoped request) is consumed on *attempt*, not on *success*, mirroring the "Redeem Sense" bug class where a resource is consumed regardless of whether the actual downstream action succeeds.

### Finding Description
`authorizer.AuthorizeRequest` in [1](#0-0)  calls `a.replayGuard.CheckAndRecord(authResult.Digest(), authResult.ExpiresAt())` immediately after successful allowlist/JWT authorization, and returns `ErrRequestAlreadySeen` on any subsequent call with the same digest, as implemented in [2](#0-1) .

This authorization step happens inside `GatewayVaultRequestProcessor.authorizeAndStamp`, called from `processCreateSecretsRequest`/`processUpdateSecretsRequest`/`processDeleteSecretsRequest`/`processListSecretIdentifiersRequest`, well before the actual vault operation is performed: [3](#0-2) .

In `GatewayHandler.HandleGatewayMessage`, the pipeline first runs `ProcessRequest` (which authorizes and consumes the replay-guard entry), and only afterward invokes `handleSecretsCreate`/`handleSecretsUpdate`/`handleSecretsDelete`/`handleSecretsList`, which call into `h.secretsService.CreateSecrets`/`UpdateSecrets`/`DeleteSecrets` and can fail independently (e.g., storage errors, encryption/threshold-decryption failures, transient backend errors): [4](#0-3) , [5](#0-4) .

Because the digest is a one-way function of the full request body (including `RequestId`), a legitimate caller (workflow owner submitting an allowlisted/JWT-authorized vault secret operation — an unprivileged actor from the DON's perspective, since authorization is per-request, not per-caller-trust) cannot simply resend the exact same request to retry after a downstream failure: the replay guard will reject it with `ErrRequestAlreadySeen` until `authResult.ExpiresAt()` is reached. The `RequestReplayGuard.CheckAndRecord`/`ClearExpired` methods confirm entries are held until expiry regardless of the outcome of the guarded action: [6](#0-5) .

This is directly analogous to the Sense `Redeemer` bug: a single-use, user-triggerable resource (there, the transferred principal; here, the allowlisted/JWT-scoped request digest) is consumed at the "attempt" step rather than gated on successful completion of the actual downstream action, so a downstream failure permanently (within the expiry window) "bricks" that specific operation for the legitimate owner.

### Impact Explanation
If a downstream `secretsService` call transiently fails (e.g. storage layer error, temporary threshold-decryption/master-key unavailability, encoding error) after authorization succeeds, the specific vault secret create/update/delete/list request becomes permanently unexecutable until the allowlist entry's `ExpiryTimestamp` or the JWT's `exp` claim passes. For a workflow owner whose allowlisted request has a long validity window, this can mean their intended secret operation (e.g., rotating/creating a critical secret) is effectively bricked for that period, causing a legitimate secrets management operation to fail with no ability to retry using the same authorized request. This is a Denial-of-Service against the vault secrets-management flow for the affected owner/request, not a fund-loss bug, but it matches the "resource consumed before action completes, permanently DOSing the action" bug class from the report.

### Likelihood Explanation
Likelihood is moderate: it requires a downstream `secretsService` operation to fail after authorization succeeds — plausible under real-world conditions (transient storage errors, key-service hiccups, or malformed-but-authorized payloads that only fail deep in `CreateSecrets`/`UpdateSecrets`/`DeleteSecrets`). No malicious intent by other parties is required; the affected owner's own single legitimate request can trigger this on any subsequent partial failure. The trigger requires only a normal unprivileged workflow-owner request going through the standard gateway path, not any special privilege.

### Recommendation
Move the replay-guard `CheckAndRecord` call so that it only marks a digest as consumed after the downstream vault operation (`CreateSecrets`/`UpdateSecrets`/`DeleteSecrets`/`ListSecretIdentifiers`) has completed successfully, or add compensating logic to release/undo the replay-guard entry if the downstream operation fails. Alternatively, decouple "authorization" (verifying the request is allowlisted/JWT-valid) from "replay protection" (preventing double-execution), and only apply replay protection after `h.secretsService.*` returns success in `gw_handler.go`.

### Proof of Concept
1. A workflow owner obtains a valid allowlisted (or JWT-signed) request to create a secret via `vault.secrets.create`.
2. The owner sends the request to the gateway; `GatewayHandler.HandleGatewayMessage` calls `ProcessRequest`, which calls `authorizer.AuthorizeRequest`, which records the request's digest in `RequestReplayGuard` (`core/capabilities/vault/authorizer.go:109`).
3. The pipeline then calls `handleSecretsCreate`, which calls `h.secretsService.CreateSecrets(ctx, &vaultCapRequest)` (`core/capabilities/vault/gw_handler.go:282`); assume this call fails due to a transient backend/storage error, returning an error response to the caller.
4. The owner resends the *exact same* authorized request (same digest) to retry.
5. `authorizer.AuthorizeRequest` re-authorizes via allowlist/JWT successfully, but `a.replayGuard.CheckAndRecord` now returns `ErrRequestAlreadySeen` (`core/capabilities/vault/request_replay_guard.go:41-43`), so the request is rejected with "request not authorized" even though the secret was never actually created.
6. The owner has no way to complete this specific secret-creation request until the allowlist entry's `ExpiryTimestamp` (or JWT `exp`) passes, since a new allowlist/JWT authorization is normally scoped to the exact same request digest.

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

**File:** core/capabilities/vault/request_replay_guard.go (L30-47)
```go
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

**File:** core/capabilities/vault/gw_handler.go (L180-236)
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

	if response == nil {
		switch req.Method {
		case vaulttypes.MethodSecretsCreate:
			response = h.handleSecretsCreate(ctx, gatewayID, req)
		case vaulttypes.MethodSecretsUpdate:
			response = h.handleSecretsUpdate(ctx, gatewayID, req)
		case vaulttypes.MethodSecretsDelete:
			response = h.handleSecretsDelete(ctx, gatewayID, req)
		case vaulttypes.MethodSecretsList:
			response = h.handleSecretsList(ctx, gatewayID, req, authResult)
		}
	}

	if err = h.gatewayConnector.SendToGateway(ctx, gatewayID, response); err != nil {
		reqLggr.Errorw("Failed to send message to gateway", "error", err)
		return err
	}

	reqLggr.Infow("Sent message to gateway", "resp", response)
	h.metrics.requestSuccess.Add(ctx, 1, metric.WithAttributes(
		attribute.String("gateway_id", gatewayID),
	))
	return nil
}
```

**File:** core/capabilities/vault/gw_handler.go (L275-292)
```go
func (h *GatewayHandler) handleSecretsCreate(ctx context.Context, gatewayID string, req *jsonrpc.Request[json.RawMessage]) *jsonrpc.Response[json.RawMessage] {
	vaultCapRequest := vaultcommon.CreateSecretsRequest{}
	if err := json.Unmarshal(*req.Params, &vaultCapRequest); err != nil {
		return h.errorResponse(ctx, gatewayID, req, api.UserMessageParseError, err)
	}

	h.lggr.Debugw("Processing authorized create secrets request", "request", vaultCapRequest.String())
	vaultCapResponse, err := h.secretsService.CreateSecrets(ctx, &vaultCapRequest)
	if err != nil {
		return h.errorResponse(ctx, gatewayID, req, api.FatalError, err)
	}

	jsonResponse, err := toJSONResponse(vaultCapResponse, req.Method)
	if err != nil {
		return h.errorResponse(ctx, gatewayID, req, api.NodeReponseEncodingError, err)
	}
	return jsonResponse
}
```
