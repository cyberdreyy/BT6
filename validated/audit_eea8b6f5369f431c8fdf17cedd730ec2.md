I found a strong analog. The `authorizer.AuthorizeRequest` in `core/capabilities/vault/authorizer.go` calls `replayGuard.CheckAndRecord` (line 109) and commits the request digest as "consumed" *before* the request has actually been dispatched to and successfully processed by the DON. If the downstream call subsequently fails (gateway timeout, quorum unreachable, node error, `sendResponse` failure, etc.), the client cannot resubmit the identical request — it will be rejected with `ErrRequestAlreadySeen` ("request was already authorized previously") until the digest's `expiresAt` window elapses. This is confirmed by the system tests explicitly working around this exact race, e.g. `system-tests/tests/smoke/cre/vault_don_test_helpers.go` and `vault_don_test.go`, which special-case "Request timed out" / "already authorized previously" as ambiguous outcomes that must be *assumed* successful because there is no way to verify or retry.

### Title
Vault gateway replay guard permanently consumes request digest before confirming successful DON processing, causing data loss on failed/ambiguous requests - (File: core/capabilities/vault/authorizer.go)

### Summary
The vault gateway's request authorization pipeline records a request's replay-protection digest as "seen" immediately upon authorization, before the request has been dispatched to and successfully processed by the DON nodes. If the subsequent processing fails, times out, or the response cannot be delivered, the client has no way to know whether the operation succeeded, and cannot safely retry the identical request because the replay guard will reject it as a duplicate until the digest naturally expires.

### Finding Description
`authorizer.AuthorizeRequest` calls `a.replayGuard.CheckAndRecord(authResult.Digest(), authResult.ExpiresAt())` [1](#0-0)  as part of authorization, which happens inside `GatewayVaultRequestProcessor.authorizeAndStamp` [2](#0-1) , which itself runs *before* the request is dispatched to DON member nodes via `HandleJSONRPCUserMessage` in the vault gateway handler [3](#0-2) .

Once `CheckAndRecord` succeeds, the digest is permanently marked as used (`g.seen[digest] = expiresAtUnix`) [4](#0-3) , and any subsequent identical request is rejected with `ErrRequestAlreadySeen` regardless of whether the first attempt actually completed. Downstream, the request can fail in multiple ways without any compensating rollback of the replay-guard state:
- The gateway relay to the DON can time out, returning `RequestTimeoutError` after `removeExpiredRequests` fires [5](#0-4) .
- `sendResponse`/`SendResponse` can itself fail after the entry is already removed from `activeRequests`, losing the in-flight request state entirely [6](#0-5) .

In all of these cases, the digest recorded by the replay guard is never released, so the exact same request (as constructed by the client, producing the same digest) is permanently unresubmittable until `expiresAt` passes — analogous to the reported Timelock bug where a queued transaction's state is discarded/consumed optimistically without confirming the operation's actual effect, forcing the operator into an unrecoverable wait rather than a safe retry.

This exact race is acknowledged and worked around in the project's own system tests rather than fixed: `shouldRetryGatewayRequest`/`isGatewayNotAllowlistedError` explicitly avoid retrying on `"Request timed out"` because "the DON likely already processed the request. Retrying...just hits the vault replay guard" [7](#0-6) , and `sendConcurrentVaultCreate` treats the replay-guard rejection as equivalent to success purely because "there is no later response payload to validate when this path fires" [8](#0-7) .

### Impact Explanation
An unprivileged client submitting a `vault.secrets.create`/`update`/`delete`/`list` request through the internet-facing gateway cannot reliably determine or recover from a failed operation. If the DON-side processing fails after authorization succeeds (network blip, node timeout, quorum not reached, response-delivery failure), the client's only recourse is to wait out the digest's `expiresAt` window before it can resubmit the identical secret-management operation — during which time secrets creation/update/deletion may be silently unconfirmed or lost, with the caller unable to distinguish "already processed" from "failed and now stuck."

### Likelihood Explanation
This requires no privileged access — any normal caller of the vault gateway (allowlisted or JWT-authenticated per existing mechanisms) can trigger it simply by having a request coincide with a transient DON/gateway failure (timeout, node error, quorum-unreachable), which the project's own system tests demonstrate occurs under realistic load conditions.

### Recommendation
Do not treat replay-guard recording as final commitment of a request. Options include: (1) only record the digest as "seen" after DON processing has been confirmed successful (i.e., move `CheckAndRecord` to occur after quorum/response success, using a separate short-lived "in-flight" reservation to prevent concurrent duplicate dispatch), or (2) release/rollback the replay-guard entry when the request ultimately fails (timeout, quorum unreachable, send failure) so the same digest can be legitimately retried, or (3) surface a distinct, retryable error/status to the caller instead of a generic `ErrRequestAlreadySeen` when the original request's outcome is unknown.

### Proof of Concept
1. Submit a valid `vault.secrets.create` request through the gateway with `req.Auth` unset (allowlist path) or a valid JWT; `AuthorizeRequest` succeeds and calls `replayGuard.CheckAndRecord(digest, expiresAt)`, marking the digest as consumed [1](#0-0) .
2. Simulate/force the DON-side quorum step to fail or time out (e.g., have fewer than quorum nodes respond within `requestTimeout`) so `removeExpiredRequests` fires and returns `api.RequestTimeoutError` to the caller [5](#0-4) .
3. Resubmit the exact same `vault.secrets.create` request (same params/digest). Observe that `AuthorizeRequest` now returns `vault.ErrRequestAlreadySeen` ("request was already authorized previously") [9](#0-8)  even though the original request never completed successfully, matching the behavior explicitly documented as a known race in `sendConcurrentVaultCreate` [8](#0-7) .
4. The caller has no way to confirm whether the secret was actually created and cannot safely retry until the digest's `expiresAt` timestamp passes.

### Citations

**File:** core/capabilities/vault/authorizer.go (L109-112)
```go
	if err := a.replayGuard.CheckAndRecord(authResult.Digest(), authResult.ExpiresAt()); err != nil {
		a.lggr.Debugw("replay guard rejected request", "method", req.Method, "requestID", req.ID, "owner", authResult.AuthorizedOwner(), "digest", authResult.Digest(), "expiresAt", authResult.ExpiresAt(), "hasAuth", req.Auth != "", "error", err)
		return nil, err
	}
```

**File:** core/capabilities/vault/gateway_vault_request_processor.go (L232-238)
```go
	p.lggr.Debugw("authorizing gateway vault request", "method", req.Method, "requestID", req.ID)
	authResult, err := p.authorizer.AuthorizeRequest(ctx, *req)
	if err != nil {
		authErr := fmt.Errorf("request not authorized: %w", err)
		p.lggr.Errorw("gateway vault request authorization failed", "method", req.Method, "requestID", req.ID, "hasAuth", req.Auth != "", "incomingOwner", incomingOwner, "error", authErr)
		return nil, authErr
	}
```

**File:** core/services/gateway/handlers/vault/handler.go (L369-393)
```go
// removeExpiredRequests removes expired requests from the pending requests map
func (h *handler) removeExpiredRequests(ctx context.Context) {
	h.mu.RLock()
	var expiredRequests []*activeRequest
	now := h.clock.Now()
	for _, userRequest := range h.activeRequests {
		if now.Sub(userRequest.createdAt) > h.requestTimeout {
			expiredRequests = append(expiredRequests, userRequest)
		}
	}
	h.mu.RUnlock()

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
}
```

**File:** core/services/gateway/handlers/vault/handler.go (L436-450)
```go
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

**File:** core/services/gateway/handlers/vault/handler.go (L797-828)
```go
func (h *handler) sendResponse(ctx context.Context, userRequest *activeRequest, resp gwhandlers.UserCallbackPayload) error {
	switch resp.ErrorCode {
	case api.StaleNodeResponseError:
	case api.FatalError:
	case api.NodeReponseEncodingError:
	case api.RequestTimeoutError:
	case api.HandlerError:
	case api.ConflictError:
	case api.LimitExceededError:
		h.metrics.requestInternalError.Add(ctx, 1, metric.WithAttributes(
			attribute.String("don_id", h.donConfig.DonId),
			attribute.String("error", resp.ErrorCode.String()),
		))
	case api.InvalidParamsError:
	case api.UnsupportedMethodError:
	case api.UserMessageParseError:
	case api.UnsupportedDONIdError:
		h.metrics.requestUserError.Add(ctx, 1, metric.WithAttributes(
			attribute.String("don_id", h.donConfig.DonId),
		))
	case api.NoError:
		h.metrics.requestSuccess.Add(ctx, 1, metric.WithAttributes(
			attribute.String("don_id", h.donConfig.DonId),
		))
	}

	err := userRequest.SendResponse(resp)
	if err != nil {
		h.lggr.Errorw("error sending response to user", "requestID", userRequest.req.ID, "error", err)
		return err
	}

```

**File:** core/capabilities/vault/request_replay_guard.go (L9-9)
```go
var ErrRequestAlreadySeen = errors.New("request was already authorized previously")
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

**File:** system-tests/tests/smoke/cre/vault_don_test_helpers.go (L230-246)
```go
func shouldRetryGatewayRequest(statusCode int, body []byte) bool {
	if isGatewayNotAllowlistedError(body) {
		return true
	}
	switch statusCode {
	case http.StatusServiceUnavailable, http.StatusBadGateway, http.StatusGatewayTimeout:
		// Gateway-to-DON timeout: the gateway gave up relaying the response, but the DON likely
		// already processed the request. Retrying the same request body just hits the vault
		// replay guard ("request was already authorized previously"). Don't retry these.
		if bytes.Contains(body, []byte("Request timed out")) {
			return false
		}
		return true
	default:
		return false
	}
}
```

**File:** system-tests/tests/smoke/cre/vault_don_test.go (L673-705)
```go
// sendConcurrentVaultCreate sends an already-allowlisted create request to the gateway and tolerates
// the replay-guard outcome. Under burst load, the gateway can time out (503 "Request timed out") while
// DON still processes the create; the test's HTTP retry then re-sends the same request digest, which
// vault's replay guard rejects with "request was already authorized previously". That error proves the
// original request was accepted and processed, so we treat it as success — there is no later response
// payload to validate when this path fires.
func sendConcurrentVaultCreate(t *testing.T, gwURL, requestID string, jsonRequest jsonrpc.Request[json.RawMessage], authorizedOwner, expectedResponseOwner string, namespaces []string) {
	t.Helper()

	authToken := jsonRequest.Auth
	stripped := outboundRequestWithoutAuth(jsonRequest)
	requestBody, err := json.Marshal(stripped)
	require.NoError(t, err, "failed to marshal vault request")
	headers := map[string]string{}
	if authToken != "" {
		headers["Authorization"] = "Bearer " + authToken
	}

	statusCode, body := sendVaultRequestToGatewayWithHeaders(t, gwURL, requestBody, headers)

	// Under burst load the gateway can return 503 "Request timed out" when it gives up relaying the
	// response, even though the DON has already processed the request. Tolerate that here — the goal
	// of this subtest is to drive concurrent load for the docker-log batching assertions below, not
	// to verify per-request response payloads.
	if statusCode == http.StatusServiceUnavailable && bytes.Contains(body, []byte("Request timed out")) {
		framework.L.Info().Str("requestID", requestID).Msg("vault create gateway-to-DON timeout; treating as success for batching load test")
		return
	}
	// Replay guard can arrive on a non-200 HTTP status after a retried gateway call; check before StatusOK.
	if bytes.Contains(body, []byte("request was already authorized previously")) {
		framework.L.Info().Str("requestID", requestID).Msg("vault create returned replay-guard error after retry; DON processed the original request — treating as success")
		return
	}
```
