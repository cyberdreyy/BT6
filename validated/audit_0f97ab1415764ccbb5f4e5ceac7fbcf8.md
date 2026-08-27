### Title
JWT Authorization for HTTP-Triggered Workflows Is Not Bound to `workflowID`, Enabling Cross-Workflow Signature Replay - ([File: core/services/gateway/handlers/capabilities/v2/workflow_metadata_handler.go])

### Summary
`WorkflowMetadataHandler.Authorize` verifies a client-supplied JWT against a JSON-RPC request digest and a replay cache keyed only by the JWT's `jti`, but the `workflowID` used to select the authorized-signer set is never included in the material that gets cryptographically signed. This is structurally the same bug class as the reported `P2pLendingProxy.isValidSignature` issue: the signature (JWT) is verified against the signer, but not against a per-instance identifier (there, the proxy contract address; here, the target `workflowID`). [1](#0-0) 

### Finding Description
`Authorize` is the entry point that authenticates an incoming HTTP-trigger request for a given workflow: [1](#0-0) 

It calls `utils.VerifyRequestJWT(token, *req)`, which recovers the signer's address from the JWT signature, checks `iss`/`exp`/`iat`, and confirms the JWT's `digest` claim matches the SHA/keccak digest of the JSON-RPC request (`req.Digest()`): [2](#0-1) 

Crucially, `workflowID` — the parameter that determines *which* workflow's authorized-signer set is being checked (`h.authorizedKeys[workflowID]`) — is passed to `Authorize` as a plain function argument, and is never part of the JWT claims, the request digest, or any other signed field. The replay-prevention cache (`h.jwtCache`) is also keyed solely by the JWT ID (`jti`), with no `workflowID` scoping: [3](#0-2) 

Consequently, if the same ECDSA signer (public key) is registered as an authorized key for more than one workflow (e.g., the same user owns multiple HTTP-trigger workflows and reuses/derives the same key, or an attacker manages to be registered as an authorized signer for two different `workflowID`s), a single JWT+request pair signed once can be presented against any `workflowID` for which that signer is authorized and the same request digest is accepted — since nothing in the signed payload restricts it to a single workflow. This mirrors the `P2pLendingProxy` root cause exactly: signatures are validated against the signer/client, but never against the "verifying contract" (here, `workflowID`) equivalent, so a signature minted for context A is equally valid replayed against context B under the same signer.

### Impact Explanation
An unprivileged HTTP client that can obtain (or itself hold) a validly-signed JWT/request pair for one workflow can replay it to trigger execution of a *different* workflow the same signer key is authorized on, without generating a new signature for that specific workflow. This can trigger unauthorized workflow runs, bypassing the intended per-workflow authorization scoping that `authorizedKeys` is meant to enforce. Because HTTP-triggered workflows can perform arbitrary actions defined by the workflow (including fund-moving or job-triggering side effects depending on workflow logic), this is a concrete authorization-bypass / request-impersonation issue reachable from an unprivileged client via the internet-facing gateway.

### Likelihood Explanation
Exploitation requires that the same public key be an authorized signer for more than one workflow — a configuration that is plausible (a workflow owner or operator commonly reuses one signing key across multiple of their own workflows) and not prevented by any code path shown. Given that configuration, replay is trivial: the attacker (or the legitimate signer acting maliciously, or anyone who intercepts a JWT+request pair) simply re-submits the same token/request against a different `workflowID` in the gateway request.

### Recommendation
Bind the JWT to the specific `workflowID` it authorizes by including `workflowID` (and ideally `donID`) as part of the signed JWT claims (e.g., an `aud` or custom `workflow_id` claim) and verifying it against the caller-supplied `workflowID` inside `Authorize`, in addition to the existing digest check. Additionally, scope the replay cache key by `(workflowID, jti)` rather than `jti` alone, so a JWT cannot be considered "already used" in one workflow's context while remaining usable in another's.

### Proof of Concept
1. A user registers the same ECDSA public key `K` as an authorized signer for two distinct HTTP-trigger workflows, `workflowID=A` and `workflowID=B` (both accepted via `syncMetadata`/`validateAuthMetadata`, which impose no cross-workflow uniqueness constraint on `AuthorizedKeys`). [4](#0-3) 
2. Client signs a JSON-RPC request with key `K`, producing a JWT via `utils.CreateRequestJWT`/`SigningMethodEth`, and submits it to trigger `workflowID=A` — `Authorize("A", token, req)` succeeds and records `jti` as used.
3. The same token/request pair is resent to trigger `workflowID=B`. Because `jwtCache.isReplay` is keyed only by `jti` (not workflow-scoped) and the digest/signature check has no `workflowID` binding, `Authorize("B", token, req)` still passes signature/digest verification for the new `workflowID`, and since `K` is also authorized for `B`, the call succeeds — unauthorizedly triggering workflow `B` with a token that was only ever intended for workflow `A`.

Note: I was not able to inspect the exact implementation of `jsonrpc.Request.Digest()` (defined in the external `chainlink-common` module, not indexed in this repo), so I cannot rule out that some upstream caller independently folds `workflowID` into the request `Params` before signing. Based on the code reachable in this repo, no such binding is enforced by `Authorize` or `VerifyRequestJWT` itself.

### Citations

**File:** core/services/gateway/handlers/capabilities/v2/workflow_metadata_handler.go (L80-108)
```go
func (h *WorkflowMetadataHandler) Authorize(workflowID string, token string, req *jsonrpc.Request[json.RawMessage]) (*gateway.AuthorizedKey, error) {
	claims, signer, err := utils.VerifyRequestJWT(token, *req)
	if err != nil {
		h.lggr.Errorw("Failed to verify JWT", "error", err)
		return nil, err
	}

	if h.jwtCache.isReplay(claims.ID) {
		h.lggr.Warnw("JWT token has already been used", "workflowID", workflowID, "signer", signer.Hex(), "jti", claims.ID)
		return nil, errors.New("JWT token has already been used. Please generate a new one with new id (jti)")
	}

	keys, exists := h.authorizedKeys[workflowID]
	if !exists {
		h.lggr.Errorw("Workflow ID not found in authorized keys", "workflowID", workflowID)
		return nil, fmt.Errorf("workflow ID %s not found", workflowID)
	}
	key := gateway.AuthorizedKey{
		KeyType:   gateway.KeyTypeECDSAEVM,
		PublicKey: strings.ToLower(signer.Hex()),
	}
	if _, exists = keys[key]; !exists {
		h.lggr.Errorw("Signer not found in authorized keys", "signer", signer.Hex())
		return nil, fmt.Errorf("signer '%s' is not authorized for workflow '%s'. Ensure that the signer is registered in the workflow definition", signer.Hex(), workflowID)
	}
	h.jwtCache.recordUsage(claims.ID)

	return &key, nil
}
```

**File:** core/services/gateway/handlers/capabilities/v2/workflow_metadata_handler.go (L326-352)
```go
func (h *WorkflowMetadataHandler) validateAuthMetadata(metadata gateway.WorkflowMetadata) error {
	if len(metadata.WorkflowSelector.WorkflowID) != workflowIDLength {
		return fmt.Errorf("invalid workflow ID: expected %d characters, got %d", workflowIDLength, len(metadata.WorkflowSelector.WorkflowID))
	}
	if len(metadata.WorkflowSelector.WorkflowOwner) != workflowOwnerLength {
		return fmt.Errorf("invalid workflow owner: expected %d characters, got %d", workflowOwnerLength, len(metadata.WorkflowSelector.WorkflowOwner))
	}
	if len(metadata.WorkflowSelector.WorkflowName) != WorkflowNameHashLength {
		return fmt.Errorf("invalid workflow name: expected %d characters, got %d", WorkflowNameHashLength, len(metadata.WorkflowSelector.WorkflowName))
	}
	if len(metadata.WorkflowSelector.WorkflowTag) == 0 || len(metadata.WorkflowSelector.WorkflowTag) > maxWorkflowTagLength {
		return fmt.Errorf("invalid workflow tag: expected non-empty and at most %d characters, got %d", maxWorkflowTagLength, len(metadata.WorkflowSelector.WorkflowTag))
	}
	if len(metadata.AuthorizedKeys) == 0 {
		return errors.New("no authorized keys")
	}
	for _, key := range metadata.AuthorizedKeys {
		if key.KeyType != gateway.KeyTypeECDSAEVM {
			return errors.New("invalid key type")
		}
		if key.PublicKey == "" || !strings.HasPrefix(key.PublicKey, "0x") || len(key.PublicKey) != ecdsaPubKeyHexLen {
			return fmt.Errorf("invalid public key: %s", key.PublicKey)
		}
		if key.PublicKey != strings.ToLower(key.PublicKey) {
			return errors.New("invalid public key: must be all lowercase")
		}
	}
```

**File:** core/utils/jwt.go (L228-304)
```go
// VerifyRequestJWT verifies a signed JWT for a JSON-RPC request
// It recovers and returns the public key used to sign the JWT, checks the issuer, validates the digest,
// and performs all validations done by jwt.ParseWithClaims() including expiration checks.
func VerifyRequestJWT[T any](tokenString string, req jsonrpc.Request[T], opts ...VerifyOption) (*JWTClaims, gethcommon.Address, error) {
	options := &verifyOptions{}
	for _, opt := range opts {
		opt(options)
	}

	maxExpiryDuration := maxJWTExpiryDuration
	if options.maxExpiryDuration != nil {
		maxExpiryDuration = *options.maxExpiryDuration
	}

	issuedAtTolerance := defaultIssuedAtTolerance
	if options.issuedAtTolerance != nil {
		issuedAtTolerance = *options.issuedAtTolerance
	}
	signedString, signature, err := splitToken(tokenString)
	if err != nil {
		return nil, gethcommon.Address{}, err
	}
	decodedSignature, err := base64.RawURLEncoding.DecodeString(signature)
	if err != nil {
		return nil, gethcommon.Address{}, fmt.Errorf("signature segment is not valid base64url: %w", err)
	}
	pubKey, err := GetSignersEthAddress([]byte(signedString), decodedSignature)
	if err != nil {
		return nil, gethcommon.Address{}, err
	}
	verifiedToken, err := jwt.ParseWithClaims(tokenString, &JWTClaims{}, func(token *jwt.Token) (any, error) {
		if token.Method.Alg() != EthereumSigningMethod.Alg() {
			return nil, fmt.Errorf("unsupported JWT 'alg': '%s'. Expected '%s'", token.Method.Alg(), EthereumSigningMethod.Alg())
		}
		if _, ok := token.Method.(*SigningMethodEth); !ok {
			return nil, jwt.ErrSignatureInvalid
		}
		return pubKey, nil
	})
	if err != nil {
		return nil, gethcommon.Address{}, err
	}
	verifiedClaims, ok := verifiedToken.Claims.(*JWTClaims)
	if !ok {
		return nil, gethcommon.Address{}, errors.New("claims payload is not in the expected format")
	}
	if !verifiedToken.Valid {
		return nil, gethcommon.Address{}, errors.New("signature or claims validation failed")
	}
	reqDigest, err := req.Digest()
	if err != nil {
		return nil, gethcommon.Address{}, err
	}
	if verifiedClaims.ID == "" {
		return nil, gethcommon.Address{}, errors.New("JWT ID (jti) is required but missing")
	}
	if verifiedClaims.ExpiresAt == nil {
		return nil, gethcommon.Address{}, errors.New("expiredAt (exp) is required but missing")
	}
	if verifiedClaims.IssuedAt == nil {
		return nil, gethcommon.Address{}, errors.New("issuedAt (iat) is required but missing")
	}
	now := time.Now()
	issuedAt := verifiedClaims.IssuedAt
	if issuedAt.After(now.Add(issuedAtTolerance)) {
		return nil, gethcommon.Address{}, fmt.Errorf("issuedAt (iat) is too far in the future (beyond tolerance of %.0f seconds)", issuedAtTolerance.Seconds())
	}
	duration := verifiedClaims.ExpiresAt.Sub(verifiedClaims.IssuedAt.Time)
	if duration > maxExpiryDuration {
		return nil, gethcommon.Address{}, fmt.Errorf("token lifetime %.0f sec exceeds the maximum allowed %.0f sec. Reduce the gap between 'iat' and 'exp'", duration.Seconds(), maxExpiryDuration.Seconds())
	}
	if verifiedClaims.Digest != "0x"+reqDigest {
		return nil, gethcommon.Address{}, fmt.Errorf("claim digest '%s' does not match calculated request digest '0x%s'", verifiedClaims.Digest, reqDigest)
	}

	return verifiedClaims, pubKey, nil
}
```
