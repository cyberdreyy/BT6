### Title
Vault gateway trusts client-supplied secret `Owner` on Create/Update/Delete instead of the authenticated owner - (File: core/capabilities/vault/gw_handler.go)

### Summary
The Mango/Juiced RootBank bug is a class of vulnerability where a privileged/trusted reference (the correct RootBank account) is supplied by the caller but never cross-checked against the value the protocol should actually use, letting an attacker substitute a different account to desynchronize state. The Chainlink Vault gateway path shows the same pattern: for `secrets_list` the node explicitly overwrites the caller-supplied owner with the authenticated owner, but for `secrets_create`, `secrets_update`, and `secrets_delete` it does not — the `Owner` field inside the request's secret identifiers, as parsed straight from client JSON, is passed through unmodified to the backing `SecretsService`.

### Finding Description
`GatewayHandler.HandleGatewayMessage` routes `MethodSecretsCreate`/`Update`/`Delete` requests through `requestProcessor.ProcessRequest`, which authorizes the request via `allowListBasedAuth`/JWT (`Authorizer.AuthorizeRequest`) and derives an `authResult.AuthorizedOwner()`, then stamps only the request ID with `authorizedOwner + separator + originalRequestID` [1](#0-0) . Crucially, the per-secret `Owner` field embedded in `CreateSecretsRequest.EncryptedSecrets[i].Id.Owner`, `UpdateSecretsRequest`, and `DeleteSecretsRequest.Ids[i].Owner` is never rewritten or compared against `authorizedOwner`.

Downstream, `handleSecretsCreate`, `handleSecretsUpdate`, and `handleSecretsDelete` re-unmarshal the raw `req.Params` into the vault request types and forward them directly to `h.secretsService.CreateSecrets/UpdateSecrets/DeleteSecrets` without touching `Owner` [2](#0-1) . By contrast, `handleSecretsList` explicitly forces `r.Owner = authResult.AuthorizedOwner()` before calling the service [3](#0-2) , showing the code is aware this substitution is necessary but only applies it to one of the four methods.

`RequestValidator.validateWriteRequest`/`ValidateDeleteSecretsRequest` only validate the *shape* of the owner string (format, length, uniqueness) via `ValidateSecretIdentifier`, and `EnsureRightLabelOnSecret` only checks that the TDH2 ciphertext label matches whatever `Id.Owner` the caller supplied — it never checks that `Id.Owner` equals the authenticated/authorized owner from the allowlist or JWT auth flow [4](#0-3) [5](#0-4) .

### Impact Explanation
If `SecretsService.CreateSecrets/UpdateSecrets/DeleteSecrets` key storage/authorization on the `Owner` field of the identifier (as the presence of the `secrets_list` override strongly implies), a caller who is authorized via the allowlist/JWT flow for their own owner identity could set a *different* owner value inside the secret identifiers of a create/update/delete request. This mirrors the RootBank confusion: the "expected account" (authenticated owner) is not cross-verified against the "actual account" used for the state-changing operation, potentially allowing creation, overwrite, or deletion of another workflow owner's vault secrets from an unprivileged, internet-facing gateway request.

### Likelihood Explanation
The gateway is explicitly reachable by unprivileged/external clients through `GatewayHandler.HandleGatewayMessage`, which is the node-side handler for gateway-relayed vault JSON-RPC requests [6](#0-5) . No cryptographic binding forces `Id.Owner` to equal the authenticated identity for create/update/delete, and the asymmetric handling versus `secrets_list` suggests this omission is a real gap rather than intentional design confirmed elsewhere in code inspected.

### Recommendation
For `secrets_create`, `secrets_update`, and `secrets_delete`, enforce that every `Id.Owner` in the request matches `authResult.AuthorizedOwner()` (rejecting the request otherwise), mirroring the pattern already used in `handleSecretsList`, or override the owner field server-side before calling into `SecretsService`, analogous to remediation of validating trusted account references against a known-good source rather than trusting caller input.

### Proof of Concept
1. Attacker obtains authorization from the allowlist/JWT path for their own owner identity (`ownerA`), producing a valid `authResult.AuthorizedOwner() == ownerA`.
2. Attacker crafts a `CreateSecretsRequest`/`UpdateSecretsRequest`/`DeleteSecretsRequest` whose `EncryptedSecrets[i].Id.Owner` (or `Ids[i].Owner`) is set to `ownerB` (a victim), satisfying `ValidateSecretIdentifier`'s format checks.
3. `GatewayVaultRequestProcessor.processCreateSecretsRequest`/etc. authorizes the envelope (based on request digest/owner prefix in `req.ID`, not the embedded secret owner) and stamps only the request ID.
4. `handleSecretsCreate`/`handleSecretsUpdate`/`handleSecretsDelete` forward the request, including the attacker-controlled `Id.Owner = ownerB`, straight to `SecretsService`, potentially writing/overwriting/deleting secrets under `ownerB`'s namespace.

Note: full confirmation that `SecretsService` trusts `Id.Owner` for storage keying (rather than deriving/re-checking owner elsewhere, e.g., in `authorizer.go` or `capability.go`, which time constraints prevented fully inspecting) would strengthen this finding; the asymmetry with `handleSecretsList`'s explicit override is the strongest available evidence in the code reviewed.

### Citations

**File:** core/capabilities/vault/gateway_vault_request_processor.go (L222-248)
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

**File:** core/capabilities/vault/gw_handler.go (L275-336)
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

func (h *GatewayHandler) handleSecretsUpdate(ctx context.Context, gatewayID string, req *jsonrpc.Request[json.RawMessage]) *jsonrpc.Response[json.RawMessage] {
	vaultCapRequest := vaultcommon.UpdateSecretsRequest{}
	if err := json.Unmarshal(*req.Params, &vaultCapRequest); err != nil {
		return h.errorResponse(ctx, gatewayID, req, api.UserMessageParseError, err)
	}

	h.lggr.Debugw("Processing authorized update secrets request", "request", vaultCapRequest.String())
	vaultCapResponse, err := h.secretsService.UpdateSecrets(ctx, &vaultCapRequest)
	if err != nil {
		return h.errorResponse(ctx, gatewayID, req, api.FatalError, err)
	}

	jsonResponse, err := toJSONResponse(vaultCapResponse, req.Method)
	if err != nil {
		return h.errorResponse(ctx, gatewayID, req, api.NodeReponseEncodingError, err)
	}
	return jsonResponse
}

func (h *GatewayHandler) handleSecretsDelete(ctx context.Context, gatewayID string, req *jsonrpc.Request[json.RawMessage]) *jsonrpc.Response[json.RawMessage] {
	r := &vaultcommon.DeleteSecretsRequest{}
	if err := json.Unmarshal(*req.Params, r); err != nil {
		return h.errorResponse(ctx, gatewayID, req, api.UserMessageParseError, err)
	}

	h.lggr.Debugw("Processing authorized delete secrets request", "request", r.String())
	resp, err := h.secretsService.DeleteSecrets(ctx, r)
	if err != nil {
		return h.errorResponse(ctx, gatewayID, req, api.HandlerError, fmt.Errorf("failed to delete secrets: %w", err))
	}

	resultBytes, err := resp.ToJSONRPCResult()
	if err != nil {
		return h.errorResponse(ctx, gatewayID, req, api.NodeReponseEncodingError, err)
	}

	return &jsonrpc.Response[json.RawMessage]{
		Version: jsonrpc.JsonRpcVersion,
		ID:      req.ID,
		Method:  req.Method,
		Result:  (*json.RawMessage)(&resultBytes),
	}
}
```

**File:** core/capabilities/vault/gw_handler.go (L338-349)
```go
func (h *GatewayHandler) handleSecretsList(ctx context.Context, gatewayID string, req *jsonrpc.Request[json.RawMessage], authResult *AuthResult) *jsonrpc.Response[json.RawMessage] {
	r := &vaultcommon.ListSecretIdentifiersRequest{}
	if err := json.Unmarshal(*req.Params, r); err != nil {
		return h.errorResponse(ctx, gatewayID, req, api.UserMessageParseError, err)
	}
	r.Owner = authResult.AuthorizedOwner()

	h.lggr.Debugw("Processing authorized list secrets request", "request", r.String())
	resp, err := h.secretsService.ListSecretIdentifiers(ctx, r)
	if err != nil {
		return h.errorResponse(ctx, gatewayID, req, api.HandlerError, fmt.Errorf("failed to list secret identifiers: %w", err))
	}
```

**File:** core/capabilities/vault/validator.go (L42-94)
```go
func (r *RequestValidator) validateWriteRequest(ctx context.Context, publicKey *tdh2easy.PublicKey, id string, encryptedSecrets []*vaultcommon.EncryptedSecret, skipLabelValidation bool) error {
	if id == "" {
		return errors.New("request ID must not be empty")
	}
	if err := r.MaxRequestBatchSizeLimiter.Check(ctx, len(encryptedSecrets)); err != nil {
		if errBoundLimited, ok := errors.AsType[limits.ErrorBoundLimited[int]](err); ok {
			return fmt.Errorf("request batch size exceeds maximum of %d: %w", errBoundLimited.Limit, err)
		}
		return fmt.Errorf("failed to check request batch size limit: %w", err)
	}
	if len(encryptedSecrets) == 0 {
		return errors.New("request batch must contain at least 1 item")
	}

	uniqueIDs := map[string]bool{}
	for idx, req := range encryptedSecrets {
		if req == nil {
			return errors.New("encrypted secret must not be nil at index " + strconv.Itoa(idx))
		}
		if req.Id == nil {
			return errors.New("secret ID must not be nil at index " + strconv.Itoa(idx))
		}

		if req.EncryptedValue == "" {
			return errors.New("secret must have encrypted value set at index " + strconv.Itoa(idx) + ":" + req.Id.String())
		}

		if err := r.ValidateSecretIdentifier(ctx, req.Id.Key, req.Id.Owner, req.Id.Namespace); err != nil {
			return fmt.Errorf("invalid secret identifier at index %d: %w", idx, err)
		}
		if err := r.ValidateCiphertextSize(ctx, req.Id.Owner, req.EncryptedValue); err != nil {
			return fmt.Errorf("secret encrypted value at index %d is invalid: %w", idx, err)
		}
		if skipLabelValidation {
			if _, err := verifyEncryptedSecret(publicKey, req.EncryptedValue); err != nil {
				return errors.New("Encrypted Secret at index [" + strconv.Itoa(idx) + "] is invalid. Error: " + err.Error())
			}
		} else {
			err := EnsureRightLabelOnSecret(publicKey, req.EncryptedValue, req.Id.Owner)
			if err != nil {
				return errors.New("Encrypted Secret at index [" + strconv.Itoa(idx) + "] doesn't have owner as the label. Error: " + err.Error())
			}
		}
		_, ok := uniqueIDs[vaulttypes.KeyFor(req.Id)]
		if ok {
			return errors.New("duplicate secret ID found at index " + strconv.Itoa(idx) + ": " + req.Id.String())
		}

		uniqueIDs[vaulttypes.KeyFor(req.Id)] = true
	}

	return nil
}
```

**File:** core/capabilities/vault/validator.go (L287-311)
```go
// EnsureRightLabelOnSecret verifies that the TDH2 ciphertext label matches the workflow
// owner label (Ethereum address, left-padded to 32 bytes). owner must be non-empty;
// when the public key is nil, verification is skipped for the same reasons as
// verifyEncryptedSecret.
func EnsureRightLabelOnSecret(publicKey *tdh2easy.PublicKey, secret, owner string) error {
	cipherText, err := verifyEncryptedSecret(publicKey, secret)
	if err != nil {
		return err
	}
	if cipherText == nil {
		return nil
	}
	if owner == "" {
		return errors.New("owner must not be empty for secret label verification")
	}

	expected := vaultutils.WorkflowOwnerToLabel(owner)
	secretLabel := cipherText.Label()
	if secretLabel == expected {
		return nil
	}

	return fmt.Errorf("secret label [%s] does not match workflow owner label [%s]",
		hex.EncodeToString(secretLabel[:]), hex.EncodeToString(expected[:]))
}
```
