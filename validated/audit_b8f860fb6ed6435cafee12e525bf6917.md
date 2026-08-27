### Title
Vault OCR-signed response validation does not bind the signature to a specific request/DON by default, enabling cross-request response confusion - ([File: core/services/gateway/handlers/vault/aggregator.go])

### Summary
The Vault gateway's `ValidateSignatures()` verifies a node-signed OCR response purely against `Payload + Context` (config digest/epoch/round), the same structural weakness as the ERC1271 report: a signature is accepted as valid proof of "signed by an authorized signer" without being cryptographically bound to the specific request it is meant to answer. Binding to the request is done only as an optional, feature-gated, best-effort string comparison after the fact, and is explicitly tolerated to be skipped.

### Finding Description
`vaulttypes.ValidateSignatures` recovers signer addresses from a hash computed only over the OCR `Context` (config digest, epoch, round) and the `Payload` bytes: [1](#0-0) 

This mirrors the ERC1271 bug class: the raw signed data does not include anything that uniquely ties the signature to the specific inbound user request (e.g., the request ID) — signature validity alone only proves "a quorum of DON signers produced this Payload for this Context," not "this Payload answers request X."

To compensate, the gateway aggregator attempts an out-of-band string check comparing the `requestId` field embedded inside the JSON payload against the expected request ID — but this check is:
1. Gated behind a feature flag (`signedResponseRequestIDEnabled`/`VaultSignedResponseRequestIDEnabled`), so when disabled, `validateUsingSignatures` is called with `validateSignedPayloadRequestID=false` and returns immediately after signature verification, with no requestID binding at all: [2](#0-1) [3](#0-2) 

2. Even when enabled, an empty `payloadRequestID` is explicitly tolerated and accepted as matching, per the code comment referencing a migration ticket: [4](#0-3) 

The `HandleNodeMessage` in `handler.go` routes any incoming node response by `resp.ID` to the pending `activeRequest` and hands the aggregated signature-checked payload straight back to the caller as the answer for that request: [5](#0-4) 

The project's own test suite documents the practical consequence of signatures not covering identifying fields: a `SignedOCRResponse` with an attacker-added out-of-schema field (a malicious `PublicKey`) still passes `ValidateSignatures` because the signature only covers `payload`+`context`: [6](#0-5) 
That specific case is mitigated by strict JSON decoding (`DisallowUnknownFields`) for the public-key path, but the general request-ID binding weakness for `MethodSecretsCreate/Update/Delete/List` responses remains dependent on the gate/backward-compatibility fallback described above.

### Impact Explanation
If the request-ID gate is disabled (which is its default/fallback behavior for the majority of the code path, and is explicitly tolerated even when enabled for un-upgraded nodes), a validly-signed OCR response for one vault owner's request could, in principle, be routed and accepted as the response to a different in-flight request keyed only by `resp.ID` — an unprivileged-actor-observable "cross-user response confusion" surface at the gateway layer: two different secret owners' secret-list/create/update/delete responses could be mismatched if the transport-level `resp.ID` collides with an unrelated request's ID (e.g., replayed or induced ID collision by a malicious/compromised node, or a race during ID reuse), since the payload itself doesn't cryptographically enforce which request it belongs to.

### Likelihood Explanation
Exploitation requires a node (or an entity able to inject/replay a node message to the gateway) to produce a validly-signed response and cause it to be delivered under a mismatched `resp.ID`, or requires the request-ID validation to be disabled/bypassed (which the code explicitly supports as a temporary state). This is a genuine architectural gap acknowledged by the code's own inline TODO/ticket reference (`CRE-4875`), but full exploitation depends on additional conditions (feature flag state, node behavior) that could not be independently verified from static code alone — I cannot confirm from the codebase whether `VaultSignedResponseRequestIDEnabled` defaults to enabled in production deployments.

### Recommendation
Include the request ID (and DON/owner-scoping context) directly in the OCR-signed data covered by `ReportToSigData` (i.e., as part of `Payload` before hashing, not just as an unauthenticated JSON field checked out-of-band), matching the ERC1271 fix pattern of binding the signed hash to request-specific/domain-specific context. Remove the "tolerate empty/missing requestId" fallback once all vault nodes are upgraded, and make request-ID binding validation mandatory (not feature-gated) for all methods using `methodSupportsSignedOCRValidation`.

### Proof of Concept
Not independently reproducible from the indexed code alone; the weakness is demonstrated by the existing test `TestVaultHandler_HandleNodeMessage_SignatureValidatedResponse_RejectsUnknownFields` [7](#0-6)  and `TestAggregator_SignedResponseMissingRequestID_Accepted` [8](#0-7) , which confirm that signature validation succeeds independent of the response's binding to a specific request.

### Citations

**File:** core/capabilities/vault/vaulttypes/types.go (L165-196)
```go
func ValidateSignatures(resp *SignedOCRResponse, allowedSigners []common.Address, minRequired int) error {
	if len(resp.Context) < 64 {
		return fmt.Errorf("context too short: expected min 64 bytes, got %d bytes", len(resp.Context))
	}

	if len(resp.Signatures) < minRequired {
		return fmt.Errorf("not enough signatures: expected min %d, got %d", minRequired, len(resp.Signatures))
	}

	// The context contains:
	// 0:32 -> config digest
	// 32:64 -> epoch + round, namely:
	//   - 0:27 -> zero padding
	//   - 27:31 -> sequence number (big endian uint32)
	//   - 31:32 -> zero round value
	// 64:96 -> extra hash (not used by the vault plugin)
	cd, epochRound := resp.Context[:32], resp.Context[32:64]
	configDigest, err := ocr2types.BytesToConfigDigest(cd)
	if err != nil {
		return fmt.Errorf("invalid config digest in signature: %w", err)
	}

	epoch := binary.BigEndian.Uint32(epochRound[27:31])
	round := epochRound[31]

	fullHash := ocr2key.ReportToSigData(ocr2types.ReportContext{
		ReportTimestamp: ocr2types.ReportTimestamp{
			ConfigDigest: configDigest,
			Epoch:        epoch,
			Round:        round,
		},
	}, []byte(resp.Payload))
```

**File:** core/services/gateway/handlers/vault/aggregator.go (L72-88)
```go
	if a.signedResponseRequestIDEnabled(ctx, l) {
		if methodSupportsSignedOCRValidation(currResp.Method) {
			currResp, err = a.validateUsingSignatures(ctx, l, don.DON, don.Nodes, requestID, currResp, true)
			if err == nil {
				return currResp, nil
			}

			l.Debugw("failed to validate signatures, falling back to quorum aggregation", "error", err)
		}
	} else {
		currResp, err = a.validateUsingSignatures(ctx, l, don.DON, don.Nodes, requestID, currResp, false)
		if err == nil {
			return currResp, nil
		}

		l.Debugw("failed to validate signatures, falling back to quorum aggregation", "error", err)
	}
```

**File:** core/services/gateway/handlers/vault/aggregator.go (L263-289)
```go
func (a *baseAggregator) validateUsingSignatures(ctx context.Context, l logger.Logger, don capabilities.DON, nodes []capabilities.Node, requestID string, resp *jsonrpc.Response[json.RawMessage], validateSignedPayloadRequestID bool) (*jsonrpc.Response[json.RawMessage], error) {
	if resp.Result == nil {
		if resp.Error != nil {
			return nil, errors.New("response has an error, cannot validate signatures. Error: " + resp.Error.Error())
		}
		return nil, errors.New("response result and error both are is nil: cannot validate signatures")
	}

	r := &vaulttypes.SignedOCRResponse{}
	err := a.unmarshal(bytes.NewReader(*resp.Result), r)
	if err != nil {
		return nil, err
	}

	signers := []common.Address{}
	for _, n := range nodes {
		signers = append(signers, common.BytesToAddress(n.Signer[0:20]))
	}

	err = vaulttypes.ValidateSignatures(r, signers, int(don.F+1))
	if err != nil {
		return nil, fmt.Errorf("failed to validate signatures: %w", err)
	}

	if !validateSignedPayloadRequestID {
		return resp, nil
	}
```

**File:** core/services/gateway/handlers/vault/aggregator.go (L296-305)
```go
	}
	// Temporarily tolerate signed OCR reports from vault nodes that have not upgraded to
	// include requestId in the signed payload. Once all vault nodes are upgraded, the
	// gateway should start rejecting responses with a missing requestId.
	// https://smartcontract-it.atlassian.net/browse/CRE-4875
	if payloadRequestID != "" && payloadRequestID != requestID {
		logger.Sugared(l).Criticalw("signed payload request id mismatch, discarding response", "requestID", requestID, "signedPayloadRequestID", payloadRequestID, "method", resp.Method)
		a.recordSignedPayloadRequestIDMismatch(ctx)
		return nil, errSignedPayloadRequestIDMismatch
	}
```

**File:** core/services/gateway/handlers/vault/handler.go (L489-530)
```go
func (h *handler) HandleNodeMessage(ctx context.Context, resp *jsonrpc.Response[json.RawMessage], nodeAddr string) error {
	l := logger.With(h.lggr, "method", resp.Method, "requestID", resp.ID, "nodeAddr", nodeAddr)
	l.Debugw("handling node response")

	if !h.nodeRateLimiter.Allow(nodeAddr) {
		l.Debugw("node is rate limited", "nodeAddr", nodeAddr)
		return nil
	}

	ar := h.getActiveRequest(resp.ID)
	if ar == nil {
		// Request is not found, so we don't need to send a response to the user
		// This can happen if a slow node responds after the request has already been completed
		l.Debugw("no pending request found for ID")
		return nil
	}

	ok := ar.addResponseForNode(nodeAddr, resp)
	if !ok {
		l.Errorw("duplicate response from node, ignoring", "nodeAddr", nodeAddr)
		return nil
	}

	copiedResponses := ar.copiedResponses()
	resp, err := h.aggregator.Aggregate(ctx, l, ar.req.ID, copiedResponses, resp)
	switch {
	case errors.Is(err, errInsufficientResponsesForQuorum):
		l.Debugw("aggregating responses, waiting for other nodes...", "error", err)
		return nil
	case err != nil:
		l.Error("quorum unobtainable, returning response to user...", "error", err, "responses", maps.Values(copiedResponses))
		return h.sendResponse(ctx, ar, h.errorResponse(ar.req, api.FatalError, err, nil))
	}

	switch resp.Method {
	case vaulttypes.MethodPublicKeyGet:
		h.tryCachePublicKeyResponse(resp, l)
	default:
		// Do nothing for other methods
	}

	return h.sendSuccessResponse(ctx, l, ar, resp)
```

**File:** core/services/gateway/handlers/vault/handler_test.go (L971-1048)
```go
func TestVaultHandler_HandleNodeMessage_SignatureValidatedResponse_RejectsUnknownFields(t *testing.T) {
	h, callback, _, _ := setupHandler(t)

	// Same signer addresses, payload, context and signatures used by TestAggregator_Valid_Signatures,
	// so the SignedOCRResponse genuinely passes signature validation (F=1 => needs F+1=2 valid signers).
	signers := []string{
		"d6da96fe596705b32bc3a0e11cdefad77feaad79000000000000000000000000",
		"327aa349c9718cd36c877d1e90458fe1929768ad000000000000000000000000",
		"e9bf394856d73402b30e160d0e05c847796f0e29000000000000000000000000",
		"efd5bdb6c3256f04489a6ca32654d547297f48b9000000000000000000000000",
	}
	nodes := makeNodes(t, signers)
	mcr := &mockCapabilitiesRegistry{F: 1, Nodes: nodes}
	h.(*handler).aggregator = &baseAggregator{
		capabilitiesRegistry:        mcr,
		vaultHandlerDonID:           h.(*handler).donConfig.DonId,
		signedResponseRequestIDGate: limits.NewGateLimiter(true),
	}

	ocrContext, err := hex.DecodeString("000ec4f6a2ba011e909eccf64628855b848e08876a1edd938a1372a9e51adff100000000000000000000000000000000000000000000000000000000000004000000000000000000000000000000000000000000000000000000000000000000")
	require.NoError(t, err)
	sig1, err := hex.DecodeString("d1067844e2849b404d903730c4cae19f090d53a578a1e8dc16ecbdc0285c1f186599108abbe0073b78bc148a6504907474ed3a6881df917e6d142cff70acfb5900")
	require.NoError(t, err)
	sig2, err := hex.DecodeString("c7517c188d297093a6f602046fad7feafe19454ee9dc269b19c8e6c01268037d1f7b423eeecbc495dd2d9a65e106bc3eab849ddfd74a10cbd4ad50c7d953bd4b01")
	require.NoError(t, err)
	payload := json.RawMessage([]byte(`{"responses":[{"error":"failed to verify ciphertext: cannot unmarshal data: unexpected end of JSON input","id":{"key":"W","namespace":"","owner":"foo"},"success":false}]}`))

	// The attacker's master public key. The signatures cover only payload+context, so adding this field
	// does not invalidate them.
	_, attackerPK, _, err := tdh2easy.GenerateKeys(1, 3)
	require.NoError(t, err)
	attackerPKBytes, err := attackerPK.Marshal()
	require.NoError(t, err)
	attackerPKHex := hex.EncodeToString(attackerPKBytes)

	// A SignedOCRResponse body with an extra, out-of-schema "publicKey" field.
	result, err := json.Marshal(struct {
		Error      string          `json:"error"`
		Payload    json.RawMessage `json:"payload"`
		Context    []byte          `json:"context"`
		Signatures [][]byte        `json:"signatures"`
		PublicKey  string          `json:"publicKey"`
	}{
		Payload:    payload,
		Context:    ocrContext,
		Signatures: [][]byte{sig1, sig2},
		PublicKey:  attackerPKHex,
	})
	require.NoError(t, err)

	// Sanity check: nothing cached yet.
	cached, cachedObj := h.(*handler).getCachedPublicKey()
	require.Nil(t, cached)
	require.Nil(t, cachedObj)

	requestID := "request_id"
	req := jsonrpc.Request[json.RawMessage]{
		ID:     requestID,
		Method: vaulttypes.MethodPublicKeyGet,
	}
	_, err = h.(*handler).newActiveRequest(req, callback)
	require.NoError(t, err)

	response := jsonrpc.Response[json.RawMessage]{
		Version: jsonrpc.JsonRpcVersion,
		ID:      requestID,
		Method:  vaulttypes.MethodPublicKeyGet,
		Result:  (*json.RawMessage)(&result),
	}

	err = h.HandleNodeMessage(t.Context(), &response, NodeOne.Address)
	require.NoError(t, err)

	// The gateway has cached the attacker-controlled master public key, purely on the basis of a
	// signature-validated response.
	_, cachedPublicKey := h.(*handler).getCachedPublicKey()
	require.Nil(t, cachedPublicKey, "expected the master public key not to be cached")
}
```

**File:** core/services/gateway/handlers/vault/aggregator_test.go (L87-100)
```go
func TestAggregator_SignedResponseMissingRequestID_Accepted(t *testing.T) {
	t.Parallel()
	payload := json.RawMessage([]byte(`{"responses":[{"error":"failed to verify ciphertext: cannot unmarshal data: unexpected end of JSON input","id":{"key":"W","namespace":"","owner":"foo"},"success":false}]}`))
	currResp, nodes := makeSignedVaultResponse(t, vaulttypes.MethodSecretsCreate, "expected-request", payload, 2)
	mcr := &mockCapabilitiesRegistry{F: 1, Nodes: nodes}
	agg := testAggregator(t, mcr, true)

	responses := map[string]jsonrpc.Response[json.RawMessage]{
		"a": currResp,
	}
	resp, err := agg.Aggregate(t.Context(), logger.Test(t), "expected-request", responses, &currResp)
	require.NoError(t, err)
	assert.Equal(t, &currResp, resp)
}
```
