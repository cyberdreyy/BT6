## Finding: Inconsistent size-limit checks between initial Vault secret validation and later Observation/blob packing cause accepted requests to silently stall

### Title
Vault write requests can pass initial ciphertext-size validation but permanently fail (stall) at Observation/blob-packing size checks - ([File: core/services/ocr2/plugins/vault/plugin.go])

### Summary
The Vault capability validates an unprivileged client's `CreateSecretsRequest`/`UpdateSecretsRequest` ciphertext against one size limit at ingestion, but the OCR3.1 reporting plugin later re-serializes that same request (plus protocol framing/response wrapper) and checks it against a different, unrelated byte limit when packing it into an Observation blob. A payload that is legal under the first check can exceed the second, causing the request to be permanently rejected deep in the pipeline after the user has already been told (implicitly, by acceptance) that the request is valid — the same "two inconsistent checks over related-but-different quantities" bug class as the referenced LiquidityPool finding.

### Finding Description
Unprivileged users submit `CreateSecretsRequest`/`UpdateSecretsRequest` messages through the internet-facing Vault gateway path. These are validated by `RequestValidator.ValidateCiphertextSize`, which decodes the hex ciphertext and checks its raw length against `MaxCiphertextLengthLimiter` (`VaultCiphertextSizeLimit`): [1](#0-0) 

This is the gate applied when writes are accepted into the node's local pending queue via `validateWriteRequest`: [2](#0-1) 

Later, during OCR3.1 report generation, the vault `ReportingPlugin` re-wraps each pending-queue item (including `SecretIdentifier`, `RequestId`, protobuf/`Any` framing, and other metadata not counted by the ciphertext-only check) and packs it into blob payloads, checking the *marshaled wire size* against a completely separate limit, `maxBlobBytes` (`VaultMaxBlobPayloadSizeLimit`): [3](#0-2) 

If a single item's marshaled size exceeds `maxBlobBytes`, the function returns a hard error rather than fitting the item, meaning that request can never be admitted into an Observation: [4](#0-3) 

The codebase itself documents this exact class of bug in a dedicated test (`TestPendingQueueStallWedgeCiphertextSizing`), which constructs a ciphertext that passes the raw-ciphertext-size limit but whose marshaled `Observation` wire size exceeds the separate observation-size limit — i.e., a request that is accepted by the ingestion-time check but is provably rejected by the downstream check: [5](#0-4) 

The presence of a purpose-built config file for this scenario (`core/scripts/cre/environment/configs/workflow-gateway-capabilities-don-vault-stall-purge.toml`, which references `VaultCiphertextSizeLimit`/`VaultMaxBlobPayloadSizeLimit`/`VaultRequestBatchSizeLimit`) further indicates this is a recognized, reachable production scenario, not merely a hypothetical.

### Impact Explanation
An unprivileged workflow owner submits a `CreateSecrets`/`UpdateSecrets` request that is accepted (passes `ValidateCiphertextSize`) but is oversized once wrapped with identifier/response/protobuf overhead. `prepareObservationPendingQueueBlobs` then fails on that single item because it cannot fit in any blob (`len(currentBatch) == 0`). The request is effectively stuck ("wedged") in the pending queue: it was validated and admitted, but the vault DON can never form a valid Observation containing it, so it can never reach state transition/consensus. This is functionally analogous to the referenced report's fund-loss pattern — the initial check gave the impression of validity/acceptance, but the operation can never actually complete due to a second, inconsistent check that accounts for extra overhead the first check ignores. Depending on how the pending queue purge/stall-handling logic behaves (which could not be fully traced with available tooling), this can silently block processing of that queue slot or DON round.

### Likelihood Explanation
This requires only an unprivileged actor submitting a Vault write request with a ciphertext sized in the narrow window between `VaultCiphertextSizeLimit` and the effective payload size implied by `VaultMaxBlobPayloadSizeLimit` minus wrapping overhead. The chainlink codebase's own test (`TestPendingQueueStallWedgeCiphertextSizing`) demonstrates such inputs are constructible with ordinary API calls (no privileged access, no malicious node/peer behavior needed), confirming the scenario is reachable from a normal client request.

### Recommendation
Align the two checks so they operate on the same accounted quantity: either (a) have `ValidateCiphertextSize` account for the full downstream wire overhead (identifier lengths, response wrapper, protobuf framing) when checking against `VaultCiphertextSizeLimit`, or (b) make `VaultMaxBlobPayloadSizeLimit` strictly larger than `VaultCiphertextSizeLimit` plus the maximum possible per-item overhead, and enforce that invariant at configuration-load time so it can't drift. Additionally, ensure a request that fails the blob-packing check produces an explicit, user-visible failure response rather than an indefinite stall.

### Proof of Concept
The existing repository test is itself a proof of concept: [6](#0-5) 
It finds a plaintext size whose resulting ciphertext is `< stallPurgeMaxCiphertextBytes` (passes `ValidateCiphertextSize`-style check) while the marshaled `CreateSecretsRequest` Observation is `> stallPurgeMaxObservationBytes` (fails the blob/observation size check used in `prepareObservationPendingQueueBlobs`), directly reproducing the "accepted-but-unprocessable" condition.

**Uncertainty**: I could not fully trace the exact production default values of `VaultCiphertextSizeLimit` vs `VaultMaxBlobPayloadSizeLimit` in `cresettings`, nor the exact downstream behavior of the pending-queue "stall/purge" handling (whether it eventually surfaces an error to the client or drops the request silently) due to tool/iteration limits. This would need to be confirmed by reading `cresettings` defaults and the pending-queue purge logic directly in a full repository checkout.

### Citations

**File:** core/capabilities/vault/validator.go (L40-94)
```go
// validateWriteRequest performs common validation for CreateSecrets and UpdateSecrets requests.
// It treats publicKey as optional, since it can be nil if the gateway nodes don't have the public key cached yet.
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

**File:** core/services/ocr2/plugins/vault/plugin.go (L564-618)
```go
// prepareObservationPendingQueueBlobs packs local-queue requests into OCR3.1 blob payloads
// (PendingQueueBlobItems), capped by byte size per blob and by maxBlobHandleCount handles.
func (r *ReportingPlugin) prepareObservationPendingQueueBlobs(
	ctx context.Context,
	seqNr uint64,
	localQueueItems []*vaulttypes.Request,
	pendingQueueHasID map[string]bool,
	maxBlobBytes int,
	maxBlobHandleCount int,
) (pendingQueueBlobPack, error) {
	var out pendingQueueBlobPack
	var currentBatch []*vaultcommon.StoredPendingQueueItem

	for i := 0; i < len(localQueueItems); i++ {
		queueItem := localQueueItems[i]
		if pendingQueueHasID[queueItem.ID()] {
			continue
		}

		anyMsg, err := anypb.New(queueItem.Payload)
		if err != nil {
			return pendingQueueBlobPack{}, fmt.Errorf("could not marshal request payload to Any: %w", err)
		}

		singleItem := &vaultcommon.StoredPendingQueueItem{
			Id:   queueItem.ID(),
			Item: anyMsg,
		}

		candidate := append(slices.Clone(currentBatch), singleItem)
		payload, err := marshalPendingQueueBlobPayload(candidate)
		if err != nil {
			return pendingQueueBlobPack{}, err
		}

		if len(payload) > maxBlobBytes {
			if len(currentBatch) == 0 {
				return pendingQueueBlobPack{}, fmt.Errorf("single pending queue item exceeds max blob payload size (%d > %d)", len(payload), maxBlobBytes)
			}
			// Current batch is full; flush it and retry the same item on the next iteration.
			var ferr error
			flushOut, flushBatch, ferr := r.flushBatch(ctx, seqNr, currentBatch, out, maxBlobBytes, maxBlobHandleCount)
			if ferr != nil {
				return pendingQueueBlobPack{}, ferr
			}
			out = flushOut
			currentBatch = flushBatch
			if out.truncated {
				break
			}
			i--
			continue
		}
		currentBatch = candidate
	}
```

**File:** system-tests/tests/smoke/cre/pending_queue_stall_sizing_test.go (L18-53)
```go
const (
	stallPurgeMaxObservationBytes = 4 * 1024
	stallPurgeMaxCiphertextBytes  = 8 * 1024
)

func marshalStallPurgeCreateObservationWireSize(t *testing.T, enc string, owner common.Address, requestID, secretID string) int {
	t.Helper()
	req := &vaultcommon.CreateSecretsRequest{
		RequestId: requestID,
		EncryptedSecrets: []*vaultcommon.EncryptedSecret{{
			Id:             &vaultcommon.SecretIdentifier{Owner: owner.Hex(), Namespace: "main", Key: secretID},
			EncryptedValue: enc,
		}},
	}
	obs := &vaultcommon.Observations{
		SortNonce: make([]byte, 32),
		Observations: []*vaultcommon.Observation{{
			Id:          requestID,
			RequestType: vaultcommon.RequestType_CREATE_SECRETS,
			Request: &vaultcommon.Observation_CreateSecretsRequest{
				CreateSecretsRequest: req,
			},
			Response: &vaultcommon.Observation_CreateSecretsResponse{
				CreateSecretsResponse: &vaultcommon.CreateSecretsResponse{
					Responses: []*vaultcommon.CreateSecretResponse{{
						Id:      req.EncryptedSecrets[0].Id,
						Success: false,
					}},
				},
			},
		}},
	}
	b, err := proto.MarshalOptions{Deterministic: true}.Marshal(obs)
	require.NoError(t, err)
	return len(b)
}
```

**File:** system-tests/tests/smoke/cre/pending_queue_stall_sizing_test.go (L67-101)
```go
func pickStallPurgeWedgePlaintextSize(t *testing.T, pk *tdh2easy.PublicKey, owner common.Address) int {
	t.Helper()
	for n := 500; n <= 4000; n += 50 {
		enc, err := vaultutils.EncryptSecretWithWorkflowOwner(strings.Repeat("x", n), pk, owner)
		require.NoError(t, err)
		raw, err := hex.DecodeString(enc)
		require.NoError(t, err)
		if len(raw) >= stallPurgeMaxCiphertextBytes {
			continue
		}
		wire := marshalStallPurgeCreateObservationWireSize(t, enc, owner, "req-stall-wedge", "stalledsecret")
		if wire > stallPurgeMaxObservationBytes {
			return n
		}
	}
	t.Fatalf("no plaintext size found with raw ciphertext below %d and observation wire above %d",
		stallPurgeMaxCiphertextBytes, stallPurgeMaxObservationBytes)
	return 0
}

func TestPendingQueueStallWedgeCiphertextSizing(t *testing.T) {
	t.Parallel()

	_, pk, _, err := tdh2easy.GenerateKeys(1, 3)
	require.NoError(t, err)
	owner := common.HexToAddress("0x1234567890123456789012345678901234567890")
	n := pickStallPurgeWedgePlaintextSize(t, pk, owner)
	enc, err := vaultutils.EncryptSecretWithWorkflowOwner(strings.Repeat("x", n), pk, owner)
	require.NoError(t, err)
	raw, err := hex.DecodeString(enc)
	require.NoError(t, err)
	wire := marshalStallPurgeCreateObservationWireSize(t, enc, owner, "req-stall-wedge", "stalledsecret")
	require.Less(t, len(raw), stallPurgeMaxCiphertextBytes)
	require.Greater(t, wire, stallPurgeMaxObservationBytes)
}
```
