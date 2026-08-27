### Title
Per-owner Vault quota checked against unauthenticated, attacker-controlled `Owner` field before real identity is established - ([File: core/capabilities/vault/gateway_vault_request_processor.go])

### Summary
This is analogous to the zkSync bug class: a limit/quota is validated using one value while the request's real, authoritative identity is determined by a separate, later step. In `GatewayVaultRequestProcessor.ProcessRequest`, request validation (including the per-owner ciphertext size limiter) is executed against the raw, client-supplied `SecretIdentifier.Owner` field *before* `AuthorizeRequest` determines the caller's real authorized identity [1](#0-0) .

### Finding Description
The documented pipeline invariant is `ValidateStructureBeforeAuth → AuthorizeRequest → Prefix ID → StampAuthorizedParams`, explicitly meaning validation (and its embedded limit checks) runs on raw request bytes **before** authorization/identity resolution [2](#0-1) .

In `processCreateSecretsRequest`/`processUpdateSecretsRequest`, the unmarshaled, still-unauthenticated request is passed straight into `p.validator.ValidateCreateSecretsRequest`/`ValidateUpdateSecretsRequest`, and only afterward is `p.authorizeAndStamp` called to run `p.authorizer.AuthorizeRequest` and derive the real `authorizedOwner` [3](#0-2) [4](#0-3) .

Inside validation, `ValidateCiphertextSize` builds the rate/size-limit context key directly from the attacker-supplied `req.Id.Owner` string (only checked for being non-empty and alphanumeric via `ValidateSecretIdentifier`, never checked against the caller's authenticated identity at this point): [5](#0-4) [6](#0-5) 

Only after this per-owner limiter check succeeds does `authorizeAndStamp` invoke the authorizer and compute `authorizedOwner := authResult.AuthorizedOwner()`, which is used to prefix the `RequestId`, but the per-secret `Owner` fields inside the payload (which is what fed the earlier limiter) are never re-validated or overwritten to force equality with `authorizedOwner` at this layer [7](#0-6) .

This mirrors the zkSync root cause precisely: the value used to check/attribute a limit (`req.Id.Owner`, attacker-controlled) is decoupled from the value that establishes the caller's true, authorized identity (`authorizedOwner`, derived later from JWT/allowlist auth) [8](#0-7) .

### Impact Explanation
Because the per-owner ciphertext-size limiter (`MaxCiphertextLengthLimiter`) is keyed on an attacker-supplied `Owner` string rather than the caller's authorized identity, a caller could submit an arbitrary/rotating `Owner` value on each request to always land in a fresh, un-throttled bucket, defeating the intended per-owner ciphertext/quota bound entirely. It could also let a caller attribute large-payload checks to an arbitrary third-party owner string, polluting or exhausting that owner's rate-limit bucket. Whether this results in stored data being mis-attributed depends on downstream authorization checks not visible in the reviewed slice (e.g., whether `Owner` is later forced to equal `authorizedOwner` prior to persistence); I could not fully confirm this from the code reviewed.

### Likelihood Explanation
Reachable directly from an unprivileged gateway client hitting `MethodSecretsCreate`/`MethodSecretsUpdate`, since `Owner` is a plain JSON field fully controlled by the request body and validation occurs before authorization resolves the real identity [9](#0-8) .

### Recommendation
Perform (or re-verify) per-owner limit checks using the authorized owner returned by `AuthorizeRequest`, not the raw client-supplied `Owner` field, or re-validate that `req.Id.Owner == authorizedOwner` for every secret in the batch immediately after authorization and before/instead of running the per-owner limiter on the unauthenticated value.

### Proof of Concept
Not independently verified end-to-end (would require exercising the live gateway + authorizer + limiter stack, which was outside the scope of static code review); the control-flow ordering itself (`validate(rawOwner)` → `authorize()` → `authorizedOwner`) is confirmed directly from source as cited above.

### Citations

**File:** core/capabilities/vault/gateway_vault_request_processor.go (L20-30)
```go
// GatewayVaultRequestProcessor orchestrates the shared gateway-routed vault JSON-RPC pipeline
// used by the gateway public handler and the node-side gateway connector handler.
//
// Pipeline invariant:
//
//	ValidateStructureBeforeAuth → AuthorizeRequest → Prefix ID → StampAuthorizedParams
//	    (no param mutation)        (on raw bytes)               (namespace + request_id)
//
// AuthorizeRequest runs while params are still digest-safe. It also applies the replay guard
// (digest deduplication) and validates that payload owners match the authorized workflow owner
// before this processor rewrites the request ID or stamps params.
```

**File:** core/capabilities/vault/gateway_vault_request_processor.go (L93-119)
```go
	if req.Params == nil {
		return nil, InvalidVaultParamsError{Method: req.Method, Err: errors.New("request params must not be nil")}
	}

	var createReq vaultcommon.CreateSecretsRequest
	if err := json.Unmarshal(*req.Params, &createReq); err != nil {
		return nil, InvalidVaultParamsError{Method: req.Method, Err: err}
	}
	if p.stripOwnerPrefixForAuth {
		createReq.RequestId = req.ID
		if err := marshalVaultParams(req, &createReq); err != nil {
			return nil, InvalidVaultParamsError{Method: req.Method, Err: err}
		}
	} else {
		createReq.RequestId = coalesceRequestID(createReq.RequestId, req.ID)
	}

	skipLabelValidation := publicKey == nil
	if err := p.validator.ValidateCreateSecretsRequest(ctx, publicKey, &createReq, skipLabelValidation); err != nil {
		return nil, p.validationError(req, err)
	}

	return p.authorizeAndStamp(ctx, req, func(prefixedRequestID string) error {
		createReq.RequestId = prefixedRequestID
		vaultutils.ApplyEncryptedSecretNamespaceDefaults(createReq.EncryptedSecrets)
		return marshalVaultParams(req, &createReq)
	})
```

**File:** core/capabilities/vault/gateway_vault_request_processor.go (L222-254)
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
```

**File:** core/capabilities/vault/validator.go (L96-110)
```go
func (r *RequestValidator) ValidateCiphertextSize(ctx context.Context, owner, encryptedValue string) error {
	rawCiphertext, err := hex.DecodeString(encryptedValue)
	if err != nil {
		return fmt.Errorf("failed to decode encrypted value: %w", err)
	}
	// TODO orgID https://smartcontract-it.atlassian.net/browse/CRE-1707
	innerCtx := contexts.WithCRE(ctx, contexts.CRE{Owner: owner})
	if err := r.MaxCiphertextLengthLimiter.Check(innerCtx, pkgconfig.Size(len(rawCiphertext))*pkgconfig.Byte); err != nil {
		if errBoundLimited, ok := errors.AsType[limits.ErrorBoundLimited[pkgconfig.Size]](err); ok {
			return fmt.Errorf("ciphertext size exceeds maximum allowed size: %s: %w", errBoundLimited.Limit, err)
		}
		return fmt.Errorf("failed to check ciphertext size limit: %w", err)
	}
	return nil
}
```

**File:** core/capabilities/vault/validator.go (L112-131)
```go
func (r *RequestValidator) ValidateSecretIdentifier(ctx context.Context, idKey, idOwner, idNamespace string) error {
	if idKey == "" {
		return errors.New("key cannot be empty")
	}
	if idOwner == "" {
		return errors.New("owner cannot be empty")
	}

	if !isValidIDComponent(idKey) || !isValidIDComponent(idOwner) || (idNamespace != "" && !isValidIDComponent(idNamespace)) {
		return errors.New("key, owner and namespace must only contain alphanumeric characters")
	}

	// TODO orgID https://smartcontract-it.atlassian.net/browse/CRE-1707
	ctx = contexts.WithCRE(ctx, contexts.CRE{Owner: idOwner})
	if err := r.MaxIdentifierOwnerLengthLimiter.Check(ctx, pkgconfig.Size(len(idOwner))); err != nil {
		if errBoundLimited, ok := errors.AsType[limits.ErrorBoundLimited[pkgconfig.Size]](err); ok {
			return fmt.Errorf("owner exceeds maximum length of %s: %w", errBoundLimited.Limit, err)
		}
		return fmt.Errorf("failed to check owner length limit: %w", err)
	}
```

**File:** core/capabilities/vault/gw_handler.go (L108-126)
```go
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
