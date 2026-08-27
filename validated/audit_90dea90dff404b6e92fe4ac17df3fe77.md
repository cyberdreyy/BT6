### Title
Secret label-to-owner binding check can be silently skipped, allowing storage of secrets with a mismatched TDH2 label - (File: `core/capabilities/vault/validator.go`)

### Summary
The GMX finding is a "fallback path skips a check the main path performs" bug class: when the primary conversion (`swapWithdrawnCollateralToPnlToken`) can't run, the code falls into a secondary branch that bypasses the `minOutputAmount` slippage protection that would normally be enforced. `core/capabilities/vault/validator.go`'s `validateWriteRequest` has the same structural bug class: a boolean `skipLabelValidation` selects between two mutually exclusive validation branches for `CreateSecrets`/`UpdateSecrets`, and one branch (`skipLabelValidation == true`) omits the owner/label binding check (`EnsureRightLabelOnSecret`) that the other branch performs, replacing it with a much weaker structural check (`verifyEncryptedSecret`).

### Finding Description
`validateWriteRequest` chooses between two validation paths per encrypted secret: [1](#0-0) 

- When `skipLabelValidation` is false, `EnsureRightLabelOnSecret` is called, which verifies that the TDH2 ciphertext's cryptographic `Label()` equals the workflow owner address claimed in `SecretIdentifier.Owner`: [2](#0-1) 

- When `skipLabelValidation` is true, only `verifyEncryptedSecret` runs, which merely checks that the ciphertext is well-formed/verifiable against the master public key — it performs **no** comparison between the embedded TDH2 label and the claimed `SecretIdentifier.Owner`: [3](#0-2) 

`skipLabelValidation` is set to `true` whenever the caller passes a `nil` public key, which happens in two reachable, unprivileged-client-facing code paths on the gateway/node boundary:

1. In the `GatewayVaultRequestProcessor`, `skipLabelValidation := publicKey == nil` — i.e., whenever the gateway-side node hasn't cached the master public key yet (e.g., right after a gateway restart), label validation is dropped entirely for that request: [4](#0-3) [5](#0-4) 

2. `GatewayHandler.HandleGatewayMessage` fetches the public key via `getMasterPublicKey` before calling the processor for `CreateSecrets`/`UpdateSecrets`, and if key retrieval fails, it short-circuits to an error response — but the *processor* itself independently derives `skipLabelValidation` from whatever `publicKey` value it is handed, so any caller path that doesn't have a warmed cache (including the in-process `Capability.CreateSecrets`/`UpdateSecrets` calling `ValidateCreateSecretsRequest`/`ValidateUpdateSecretsRequest` directly with `skipLabelValidation=false` hard-coded, versus the gateway path relying on cache state) creates two structurally different enforcement levels for the same request type: [6](#0-5) [7](#0-6) 

Because the identifier-owner authorization check (`validateSecretOwnersMatchAuthorized`) only compares the plaintext `SecretIdentifier.Owner` string field against the authorizer's resolved owner, and never itself inspects the ciphertext label, `EnsureRightLabelOnSecret` is the *only* code path that ties the ciphertext's cryptographic binding to the claimed identifier: [8](#0-7) 

TDH2 encryption to a label is a public-key operation — any unprivileged client with the master public key (obtainable via `MethodPublicKeyGet`) can construct a ciphertext with an arbitrary label of their choosing, not necessarily their own. This is precisely why `EnsureRightLabelOnSecret` exists: to prevent a caller from writing a secret entry that declares itself as belonging to owner `X` (satisfying the identifier-owner check) while the ciphertext is actually labeled for owner `Y`. When the public-key cache is cold (documented explicitly as a normal, attacker-triggerable-adjacent condition — "immediately after gateway reboots"), this binding check silently disappears, and a mismatched (identifier-owner, ciphertext-label) pair can be persisted.

### Impact Explanation
This directly matches the accepted impact classes: **cross-user response confusion** / weakened authorization boundary at the internet-facing gateway path. Storing a secret whose declared owner and cryptographic label diverge undermines the core security invariant the vault relies on to route/authorize decryption per-owner. Depending on how the downstream threshold-decryption/`GetSecrets` flow resolves label vs. stored owner, this can lead to secrets being unusable, silently corrupted, or (if decrypt-time authorization trusts the embedded label rather than re-validating it against the stored identifier) potentially exposed to or associated with the wrong owner context. I could not fully trace the decrypt-time enforcement in the code retrieved, so the exact blast radius (denial-of-service on the secret vs. genuine cross-owner disclosure) is not fully confirmed from the available context.

### Likelihood Explanation
The trigger condition (`publicKey == nil`) is explicitly documented in code as occurring during normal, non-adversarial operation ("gateway cache isn't populated yet, immediately after gateway reboots"), meaning any unprivileged, already-allowlisted client (a normal workflow owner) can hit this degraded-validation branch simply by racing a `CreateSecrets`/`UpdateSecrets` call against a gateway node restart — no privileged access or malicious-node/peer behavior is required.

### Recommendation
Do not silently downgrade to `verifyEncryptedSecret` when the public key is unavailable; either reject the write request until the public key is available, or defer/queue validation, ensuring `EnsureRightLabelOnSecret` (or an equivalent label-to-owner binding check) is unconditionally enforced before a `CreateSecrets`/`UpdateSecrets` request is accepted and persisted.

### Proof of Concept
1. Restart (or simulate a cold cache on) a Vault gateway-connected node so `GatewayHandler.cachedMasterPublicKey` is `nil` and `MasterPublicKeyFromSecretsService` has not yet completed/cached — reachable via `getMasterPublicKey`.
2. As an allowlisted workflow owner `A`, fetch the master public key via `MethodPublicKeyGet`.
3. Construct a TDH2 ciphertext with `Label = ownerB` (any other address) using only the public master key (no private material of `B` required).
4. Send `MethodSecretsCreate` with `SecretIdentifier{Owner: A, Key: k}` and the mismatched ciphertext, during the window where the processing node's public key cache is still `nil`, causing `GatewayVaultRequestProcessor.processCreateSecretsRequest` to compute `skipLabelValidation = true` and call the identifier/owner check (which passes, since `Id.Owner == A`) but skip `EnsureRightLabelOnSecret`, resulting in a persisted secret record where the identifier owner (`A`) and the ciphertext's true cryptographic label (`B`) diverge — a state that is normally impossible to reach when the public key is warm.

### Citations

**File:** core/capabilities/vault/validator.go (L75-84)
```go
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
```

**File:** core/capabilities/vault/validator.go (L291-311)
```go
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

**File:** core/capabilities/vault/validator.go (L313-328)
```go
func verifyEncryptedSecret(publicKey *tdh2easy.PublicKey, secret string) (*tdh2easy.Ciphertext, error) {
	cipherBytes, err := hex.DecodeString(secret)
	if err != nil {
		return nil, errors.New("failed to decode encrypted value:" + err.Error())
	}
	if publicKey == nil {
		// Public key can be nil if gateway cache isn't populated yet (immediately after gateway reboots).
		// Ok to not validate in such cases, since this validation also runs on Vault Nodes.
		return nil, nil
	}

	cipherText := &tdh2easy.Ciphertext{}
	if err := cipherText.UnmarshalVerify(cipherBytes, publicKey); err != nil {
		return nil, errors.New("failed to verify encrypted value:" + err.Error())
	}
	return cipherText, nil
```

**File:** core/capabilities/vault/gateway_vault_request_processor.go (L110-119)
```go
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

**File:** core/capabilities/vault/gateway_vault_request_processor.go (L144-147)
```go
	skipLabelValidation := publicKey == nil
	if err := p.validator.ValidateUpdateSecretsRequest(ctx, publicKey, &updateReq, skipLabelValidation); err != nil {
		return nil, p.validationError(req, err)
	}
```

**File:** core/capabilities/vault/gw_handler.go (L187-199)
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
```

**File:** core/capabilities/vault/capability.go (L163-187)
```go
func (s *Capability) CreateSecrets(ctx context.Context, request *vaultcommon.CreateSecretsRequest) (*vaulttypes.Response, error) {
	s.lggr.Debugw("received create secrets request", "requestID", request.RequestId, "request", request.String())
	if err := validateEncryptedSecretsUniformOwners(request.EncryptedSecrets); err != nil {
		return nil, err
	}
	err := s.ValidateCreateSecretsRequest(ctx, s.publicKey.Get(), request, false)
	if err != nil {
		s.lggr.Debugw("failed validation checks", "requestID", request.RequestId, "err", err)
		return nil, err
	}
	return s.handleRequest(ctx, request.RequestId, request)
}

func (s *Capability) UpdateSecrets(ctx context.Context, request *vaultcommon.UpdateSecretsRequest) (*vaulttypes.Response, error) {
	s.lggr.Debugw("received update secrets request", "requestID", request.RequestId, "request", request.String())
	if err := validateEncryptedSecretsUniformOwners(request.EncryptedSecrets); err != nil {
		return nil, err
	}
	err := s.ValidateUpdateSecretsRequest(ctx, s.publicKey.Get(), request, false)
	if err != nil {
		s.lggr.Debugw("failed validation checks", "requestID", request.RequestId, "err", err)
		return nil, err
	}
	return s.handleRequest(ctx, request.RequestId, request)
}
```

**File:** core/capabilities/vault/authorizer.go (L199-215)
```go
func validateEncryptedSecretOwnerMismatch(encryptedSecrets []*vaultcommon.EncryptedSecret, workflowOwner string) error {
	if len(encryptedSecrets) == 0 {
		return errors.New("request batch must contain at least 1 item")
	}
	for idx, encryptedSecret := range encryptedSecrets {
		if encryptedSecret == nil {
			return fmt.Errorf("encrypted secret must not be nil at index %d", idx)
		}
		if encryptedSecret.Id == nil {
			return fmt.Errorf("secret ID must not be nil at index %d", idx)
		}
		if vaultutils.NormalizeOwner(encryptedSecret.Id.Owner) != vaultutils.NormalizeOwner(workflowOwner) {
			return fmt.Errorf("encrypted secret owner at index %d %q does not match authorized workflow owner %q", idx, encryptedSecret.Id.Owner, workflowOwner)
		}
	}
	return nil
}
```
