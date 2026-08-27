## Analysis

The Buffer finding is about a signature-based authorization scheme where the signed payload doesn't include a domain separator (`chainId`), allowing a signature/authorization computed for one execution context to be replayed in a different one.

The closest concrete analog in this repository is in the gateway's workflow JWT authorization flow, `WorkflowMetadataHandler.Authorize`, which suffers from the same class of bug: **the cryptographically-verified digest never binds to the `workflowID` (the authorization "domain"), so the domain check is done out-of-band, after signature verification, against a per-workflow key set** — the same pattern the report calls out for missing `chainId`.

`Authorize` receives `workflowID`, `token`, and `req`, but the JWT's signed `digest` claim is computed purely from the JSON‑RPC `req` (method + params) via `req.Digest()`, and `VerifyRequestJWT` only checks that claim against `req.Digest()` — it never folds `workflowID` into the signed material: [1](#0-0) 

The digest computed and signed by the client (`CreateRequestJWT`) and the digest re-verified server-side (`VerifyRequestJWT`) are both derived only from `req`, with `workflowID` entirely absent from the cryptographic binding: [2](#0-1) [3](#0-2) 

After the digest check succeeds, authorization is decided solely by looking up the signer's recovered address in `h.authorizedKeys[workflowID]` — a map keyed by `workflowID` that is populated independently of the signed digest: [4](#0-3) 

Because `workflowID` is not part of the signed digest, if the *same* ECDSA signer key is registered as an authorized key for two different `workflowID`s (a supported/expected scenario for shared owner keys across workflows), a JWT minted to authorize a request for `workflowID` A will also pass digest verification for an identical-bodied request submitted with `workflowID` B — this is a cross-workflow (cross-domain) replay, directly analogous to a cross-chain replay caused by a missing `chainId` in signed content.

I was unable to fully confirm from the available index whether the request `params` for every gateway-routed method (e.g. `MethodWorkflowExecute`) always uniquely embeds the `workflowID` inside the signed body (which would make the digest implicitly domain-bound and neutralize the issue in practice). The call site in `core/services/gateway/handlers/capabilities/v2/http_trigger_handler.go` that invokes `Authorize` was found but its content was not retrievable within the tool-call budget, so I cannot state definitively whether an attacker-controlled/attacker-observable request body lacking `workflowID` is reachable end-to-end. Given this uncertainty about full exploitability, I present this as a lower-confidence architectural analog rather than a fully proven, ready-to-exploit vulnerability.

### Title
Missing domain (workflowID) binding in gateway JWT request-digest verification enables cross-workflow signature replay - (File: core/services/gateway/handlers/capabilities/v2/workflow_metadata_handler.go)

### Summary
`WorkflowMetadataHandler.Authorize` verifies a client-supplied JWT whose signed `digest` claim covers only the JSON-RPC `method`/`params`, never the `workflowID` used to select the authorized-signer set. `workflowID` is checked afterward via a plain map lookup, not as part of the signed content — the same missing-domain-separator pattern as the Buffer report's missing `chainId`.

### Finding Description
`CreateRequestJWT`/`VerifyRequestJWT` compute and check the signed digest solely from `req.Digest()` [5](#0-4) [3](#0-2) . `Authorize(workflowID, token, req)` uses this digest check, then separately looks up the recovered signer in `h.authorizedKeys[workflowID]` [6](#0-5) . Because `workflowID` never enters the hash that is signed, the authorization "domain" is not cryptographically bound to the signature, mirroring the Buffer `_validateSigner()` issue where `block.chainId` was omitted from signed data.

### Impact Explanation
If a signer key is authorized for multiple `workflowID`s (a supported configuration, since `authorizedKeys` is keyed independently per workflow), a valid JWT minted for one workflow could satisfy the digest check for a request submitted under a different `workflowID`, provided the JSON-RPC body content matches. This could result in unauthorized cross-workflow request authorization.

### Likelihood Explanation
Exploitability depends on whether `req` bodies across distinct `workflowID`s can realistically collide (i.e., whether `workflowID` is otherwise embedded in `params`). This could not be confirmed from the retrieved code, so likelihood is uncertain/moderate rather than confirmed-high.

### Recommendation
Include `workflowID` (and any other authorization-scoping identifiers) directly in the JWT's signed digest computation (e.g., hash `workflowID || req.Digest()`), analogous to including `block.chainId` in signed data, so the signature cannot be replayed across different authorization domains.

### Proof of Concept
Not fully constructible from available code: it requires confirming (a) a signer key registered as an authorized key for two distinct `workflowID`s, and (b) a JSON‑RPC request body for `MethodWorkflowExecute` (or another routed method) that does not otherwise distinguish `workflowID`, so that a valid JWT+digest for workflow A also verifies against workflow B via `Authorize`. This end-to-end reachability was not verified within the available index.

### Citations

**File:** core/services/gateway/handlers/capabilities/v2/workflow_metadata_handler.go (L80-107)
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
```

**File:** core/utils/jwt.go (L168-216)
```go
func CreateRequestJWT[T any](req jsonrpc.Request[T], opts ...Option) (*jwt.Token, error) {
	// Apply options
	options := &jwtOptions{}
	for _, opt := range opts {
		opt(options)
	}

	expiryDuration := maxJWTExpiryDuration
	if options.expiryDuration != nil {
		expiryDuration = *options.expiryDuration
	}

	digest, err := req.Digest()
	if err != nil {
		return nil, err
	}

	var issuer string
	if options.issuer != nil {
		issuer = *options.issuer
	}

	var subject string
	if options.subject != nil {
		subject = *options.subject
	}

	var audience []string
	if options.audience != nil {
		audience = options.audience
	}

	now := time.Now()
	jti := uuid.New().String()

	claims := JWTClaims{
		Digest: "0x" + digest,
		RegisteredClaims: jwt.RegisteredClaims{
			ID:        jti,
			Issuer:    issuer,
			Subject:   subject,
			Audience:  jwt.ClaimStrings(audience),
			ExpiresAt: jwt.NewNumericDate(now.Add(expiryDuration)),
			IssuedAt:  jwt.NewNumericDate(now),
		},
	}

	return jwt.NewWithClaims(&SigningMethodEth{}, claims), nil
}
```

**File:** core/utils/jwt.go (L277-301)
```go
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
```
