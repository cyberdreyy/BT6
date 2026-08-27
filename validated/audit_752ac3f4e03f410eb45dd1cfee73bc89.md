### Title
Timeout error path leaks raw per-node JSON-RPC responses to the requesting user - ([File: core/services/gateway/handlers/vault/handler.go])

### Summary
`removeExpiredRequests` builds a diagnostic string by `%v`-formatting every node's raw `jsonrpc.Response[json.RawMessage]` (including `Result`/`Error` payloads) and sends that string back to the requesting user both inside the error message and as the JSON-RPC error `data` field, whenever a vault request expires without reaching quorum. Because each node's `Result` for vault methods (e.g. secrets responses) can legitimately carry `EncryptedValue`/`EncryptedDecryptionKeyShares`/error detail per [1](#0-0) , a minority/partial set of node responses collected before timeout is disclosed to an unprivileged internet client without going through the normal signature/quorum-validated aggregation path.

### Finding Description
`HandleNodeMessage` accumulates each vault node's raw response in `activeRequest.responses` as it arrives [2](#0-1) . If quorum/signature validation never completes before `requestTimeout` elapses, `removeExpiredRequests` is invoked periodically by the handler's cleanup goroutine [3](#0-2) .

That function does:
```go
responses := er.copiedResponses()
var nodeResponses strings.Builder
for nodeKey, nodeResponse := range responses {
    _, _ = fmt.Fprintf(&nodeResponses, "%s ---::: %v               ", nodeKey, nodeResponse)
}
nodeResponsesStr := nodeResponses.String()
err := h.sendResponse(ctx, er, h.errorResponse(er.req, api.RequestTimeoutError, errors.New("request expired without getting quorum of responses from nodes. Available responses: "+nodeResponsesStr), []byte(nodeResponsesStr)))
``` [4](#0-3) 

This concatenates the full `%v` representation of every node's `jsonrpc.Response[json.RawMessage]` — which embeds the raw `Result`/`Error` bytes returned by each vault DON node — into both the human-readable error message text and the raw `data` bytes of the JSON-RPC error response. `errorResponse` has a `switch` on `errorCode` that redacts/rewrites the error text for several codes (`NodeReponseEncodingError`, `InvalidParamsError`, `UnsupportedMethodError`, `UserMessageParseError`), but `api.RequestTimeoutError` has an empty case and performs no redaction, so `err` and `data` pass through unmodified into `h.codec.EncodeNewErrorResponse(...)` and are returned directly to the caller via `sendResponse` → `userRequest.SendResponse(resp)` [5](#0-4) .

Per-node vault responses for secrets operations can contain sensitive material intended only to reach the requester after the aggregator validates signatures/quorum — e.g. `SecretData.EncryptedValue` and `EncryptedDecryptionKeyShares` for `GetSecrets`-style flows, and per-secret success/error detail for create/update responses [1](#0-0) . The normal, validated path (`baseAggregator.Aggregate` → `validateUsingSignatures`/`validateUsingQuorum`) only returns a response once signatures are verified against DON signers or a supermajority digest match is found [6](#0-5) [7](#0-6) . The `removeExpiredRequests` path completely bypasses this validation and dumps every raw per-node response — including responses from nodes that may be malicious, out of quorum, or simply first-to-arrive with unverified content — straight to the requesting client.

An attacker who is an ordinary, unprivileged, signed gateway user only needs to trigger a request that fails to reach quorum before timeout (e.g. by sending a request during partial DON unavailability/slow response conditions, or one crafted so a minority of nodes disagree and quorum is unattainable) to receive this diagnostic dump in the error response.

### Impact Explanation
This matches "server credential/secret disclosure" impact: raw per-node JSON-RPC response bodies for vault secrets operations are returned to an unauthorized/unprivileged requester without cryptographic validation, potentially exposing `EncryptedValue`, `EncryptedDecryptionKeyShares`, node-specific error text, or other node-internal response detail that should never reach an end user outside the validated aggregation path. Even when payloads are ciphertext/shares rather than raw plaintext, disclosure of unvalidated per-node internals (including signatures/config-digest-adjacent Result content) undermines the confidentiality guarantees the aggregator quorum/signature check is designed to enforce, and could aid further cryptanalysis or leak information about individual node behavior/state that should remain internal to the DON.

### Likelihood Explanation
No special privilege is required beyond being able to send a signed gateway vault request (any externally-owned key can obtain gateway authorization for its own owner-scoped secrets per `requestProcessor.ProcessRequest`/`AuthorizedOwner()`) [8](#0-7) . The trigger condition — a request that fails to reach quorum before `requestTimeout` (default configurable, `RequestTimeoutSec`) — is realistically reachable during normal operational conditions such as node latency, partial DON outages, or by an attacker submitting requests when the DON is known to be degraded; it does not require any node compromise, and is repeatable per request ID.

### Recommendation
In `removeExpiredRequests`, stop building `nodeResponsesStr` from the raw per-node responses and stop passing it to the user. The timeout error returned to the client should contain a generic, non-data-carrying message (e.g. request ID and timeout duration only); log the raw per-node responses at `Debug`/`Error` level server-side (as is already done elsewhere, e.g. in `HandleNodeMessage`'s quorum-unobtainable branch which correctly passes `nil` as `data`) instead of embedding them in the outbound `errorResponse` `err`/`data` arguments. Additionally add an explicit redaction case for `api.RequestTimeoutError` in `errorResponse`'s switch statement to guarantee no caller can regress this by accidentally passing node data through that code path in the future.

### Proof of Concept
Go table/unit test plan for `core/services/gateway/handlers/vault/handler_test.go`:
1. Construct a `handler` via existing `setupHandler(t)` helper with a short `requestTimeout` (e.g. via `cfg.RequestTimeoutSec = 1` and a mocked/advance `clockwork.Clock`).
2. Submit a `MethodSecretsCreate`/`MethodSecretsList` request via `HandleJSONRPCUserMessage`.
3. Simulate one or two (but fewer than quorum `2F+1`) node responses via `HandleNodeMessage`, using a `jsonrpc.Response[json.RawMessage]` whose `Result` contains a marker secret string such as `"EncryptedValue":"DEADBEEF-SECRET-MARKER"`.
4. Advance the fake clock past `requestTimeout` and invoke `h.removeExpiredRequests(ctx)` directly (it's an unexported method, callable from an in-package test).
5. Assert on the `UserCallbackPayload` delivered to the callback: assert that `resp.RawResponse` (both the JSON-RPC `error.message` and `error.data` fields) does **not** contain the marker string `"DEADBEEF-SECRET-MARKER"` or the node address, i.e. `require.NotContains(t, string(resp.RawResponse), "DEADBEEF-SECRET-MARKER")`.
6. Current behavior: this assertion fails, because `nodeResponsesStr` (built from the raw node `Result`) is embedded in both the error message and `data` fields, proving the leak described above.

### Citations

**File:** core/services/ocr2/plugins/vault/plugin.go (L1215-1223)
```go
	return &vaultcommon.SecretResponse{
		Id: id,
		Result: &vaultcommon.SecretResponse_Data{
			Data: &vaultcommon.SecretData{
				EncryptedValue:               hex.EncodeToString(secret.EncryptedSecret),
				EncryptedDecryptionKeyShares: shares,
			},
		},
	}, nil
```

**File:** core/services/gateway/handlers/vault/handler.go (L287-304)
```go
		go func() {
			ctx, cancel := h.stopCh.NewCtx()
			defer cancel()
			ticker := h.clock.NewTicker(defaultCleanUpPeriod)
			tickerVaultPublicKeyRefresh := h.clock.NewTicker(1 * time.Minute)
			defer ticker.Stop()
			defer tickerVaultPublicKeyRefresh.Stop()
			for {
				select {
				case <-ticker.Chan():
					h.removeExpiredRequests(ctx)
				case <-tickerVaultPublicKeyRefresh.Chan():
					// periodically, fetch vault public key, so we can cache it
					h.fetchVaultPublicKey(ctx)
				case <-h.stopCh:
					return
				}
			}
```

**File:** core/services/gateway/handlers/vault/handler.go (L381-392)
```go
	for _, er := range expiredRequests {
		responses := er.copiedResponses()
		var nodeResponses strings.Builder
		for nodeKey, nodeResponse := range responses {
			_, _ = fmt.Fprintf(&nodeResponses, "%s ---::: %v               ", nodeKey, nodeResponse)
		}
		nodeResponsesStr := nodeResponses.String()
		err := h.sendResponse(ctx, er, h.errorResponse(er.req, api.RequestTimeoutError, errors.New("request expired without getting quorum of responses from nodes. Available responses: "+nodeResponsesStr), []byte(nodeResponsesStr)))
		if err != nil {
			h.lggr.Errorw("error sending response to user", "requestID", er.req.ID, "error", err)
		}
	}
```

**File:** core/services/gateway/handlers/vault/handler.go (L435-450)
```go
	_, cachedPublicKey := h.getCachedPublicKey()
	authorized, err := h.requestProcessor.ProcessRequest(ctx, &req, cachedPublicKey)
	if err != nil {
		if vaultcap.IsInvalidVaultParamsError(err) {
			return h.sendImmediateUserResponse(ctx, req, callback, api.InvalidParamsError, err)
		}
		h.lggr.Errorw("request not authorized", "method", req.Method, "requestID", req.ID, "hasAuth", req.Auth != "", "error", err)
		return errors.New("request not authorized: " + err.Error())
	}
	authorizedOwner := authorized.AuthResult.AuthorizedOwner()

	h.lggr.Debugw("handling authorized vault request", "method", req.Method, "requestID", req.ID, "authorizedOwner", authorizedOwner)
	ar, activeRequestErr := h.newActiveRequest(req, callback)
	if activeRequestErr != nil {
		return activeRequestErr
	}
```

**File:** core/services/gateway/handlers/vault/handler.go (L489-521)
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
```

**File:** core/services/gateway/handlers/vault/handler.go (L744-795)
```go
func (h *handler) errorResponse(
	req jsonrpc.Request[json.RawMessage],
	errorCode api.ErrorCode,
	err error,
	data []byte,
) gwhandlers.UserCallbackPayload {
	switch errorCode {
	case api.FatalError:
	case api.NodeReponseEncodingError:
		h.lggr.Errorw(err.Error(), "requestID", req.ID)
		// Intentionally hide the error from the user
		err = errors.New(errorCode.String())
	case api.InvalidParamsError:
		paramsStr := ""
		if req.Params != nil {
			paramsStr = string(*req.Params)
		}
		h.lggr.Errorw("invalid params", "requestID", req.ID, "params", paramsStr)
		err = errors.New("invalid params error: " + err.Error())
	case api.UnsupportedMethodError:
		h.lggr.Errorw("unsupported method", "requestID", req.ID, "method", req.Method, "error", err.Error())
		err = errors.New("unsupported method(" + req.Method + "): " + err.Error())
	case api.UserMessageParseError:
		h.lggr.Errorw("user message parse error", "requestID", req.ID, "error", err.Error())
		err = errors.New("user message parse error: " + err.Error())
	case api.NoError:
	case api.UnsupportedDONIdError:
	case api.ConflictError:
	case api.HandlerError:
	case api.LimitExceededError:
	case api.RequestTimeoutError:
	case api.StaleNodeResponseError:
		// Unused in this handler
	}

	// Strip the owner prefix from the json response ID before sending it back to the user
	// This ensures compliance with JSONRPC 2.0 spec, which requires response id to match request id
	index := strings.Index(req.ID, vaulttypes.RequestIDSeparator)
	if index != -1 {
		req.ID = req.ID[index+2:]
	}

	return gwhandlers.UserCallbackPayload{
		RawResponse: h.codec.EncodeNewErrorResponse(
			req.ID,
			api.ToJSONRPCErrorCode(errorCode),
			err.Error(),
			data,
		),
		ErrorCode: errorCode,
	}
}
```

**File:** core/services/gateway/handlers/vault/aggregator.go (L66-96)
```go
func (a *baseAggregator) Aggregate(ctx context.Context, l logger.Logger, requestID string, resps map[string]jsonrpc.Response[json.RawMessage], currResp *jsonrpc.Response[json.RawMessage]) (*jsonrpc.Response[json.RawMessage], error) {
	don, err := a.donForVaultCapability(ctx)
	if err != nil {
		return nil, fmt.Errorf("failed to get DON for vault capability: %w", err)
	}

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

	currResp, err = a.validateUsingQuorum(don.DON, resps, l)
	if err != nil {
		return nil, fmt.Errorf("failed to validate using quorum: %w", err)
	}

	return currResp, nil
}
```

**File:** core/services/gateway/handlers/vault/aggregator.go (L156-206)
```go
func (a *baseAggregator) validateUsingQuorum(don capabilities.DON, resps map[string]jsonrpc.Response[json.RawMessage], l logger.Logger) (*jsonrpc.Response[json.RawMessage], error) {
	requiredQuorum := int(2*don.F + 1)

	if len(resps) < requiredQuorum {
		return nil, errInsufficientResponsesForQuorum
	}

	shaToCount := map[string]int{}
	maxShaToCount := 0
	for _, r := range resps {
		sha, err := a.sha(&r)
		if err != nil {
			l.Errorw("failed to compute digest of response during quorum validation, skipping...", "error", err)
			continue
		}
		shaToCount[sha]++
		if shaToCount[sha] > maxShaToCount {
			maxShaToCount = shaToCount[sha]
		}
	}

	var qualifiedDigests []string
	for sha, n := range shaToCount {
		if n >= requiredQuorum {
			qualifiedDigests = append(qualifiedDigests, sha)
		}
	}
	if len(qualifiedDigests) > 0 {
		slices.Sort(qualifiedDigests)
		want := qualifiedDigests[0]
		for _, k := range slices.Sorted(maps.Keys(resps)) {
			r := resps[k]
			sha, err := a.sha(&r)
			if err != nil {
				continue
			}
			if sha == want {
				out := r
				return &out, nil
			}
		}
	}

	remainingResponses := len(don.Members) - len(resps)
	if maxShaToCount+remainingResponses < requiredQuorum {
		l.Warnw("quorum unattainable for request", "requiredQuorum", requiredQuorum, "remainingResponses", remainingResponses, "maxShaToCount", maxShaToCount, "remainingResponses", remainingResponses, "allResponses", resps)
		return nil, errors.New(errQuorumUnobtainable.Error() + ". RequiredQuorum=" + strconv.Itoa(requiredQuorum) + ". maxShaToCount=" + strconv.Itoa(maxShaToCount) + " remainingResponses=" + strconv.Itoa(remainingResponses))
	}

	return nil, errInsufficientResponsesForQuorum
}
```
