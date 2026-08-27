### Title
Memory leak in `handler.sendResponse` due to missing `activeRequests` cleanup on `SendResponse` error path - ([File: core/services/gateway/handlers/vault/handler.go])

### Summary
`sendResponse` only calls `delete(h.activeRequests, userRequest.req.ID)` after `userRequest.SendResponse(resp)` returns without error; if `SendResponse` fails, the function returns the error immediately and the `activeRequest` entry is never removed from the map. Because `Callback.SendResponse` uses a one-shot `sent.CompareAndSwap(false, true)` guard, exactly this failure occurs whenever two independent code paths race to deliver a response for the same `req.ID` — a scenario that can arise from a normal node-response vs. timeout race and is influenced by attacker-controlled timing and request IDs.

### Finding Description
In `handler.sendResponse` [1](#0-0) , the cleanup logic is:
```go
err := userRequest.SendResponse(resp)
if err != nil {
    h.lggr.Errorw("error sending response to user", ...)
    return err
}
h.mu.Lock()
defer h.mu.Unlock()
delete(h.activeRequests, userRequest.req.ID)
```
The `delete` only executes on the success path. `activeRequest` embeds `gwhandlers.Callback`, backed by `common.Callback.SendResponse` [2](#0-1) , which uses `sent.CompareAndSwap(false, true)` to allow only a single successful send per callback; any second caller gets `errors.New("response already sent...")`.

Two internal paths can call `sendResponse` for the same `ar`: `HandleNodeMessage` (on quorum/aggregation completion or error) [3](#0-2)  and the periodic `removeExpiredRequests` timeout sweep [4](#0-3) . If both race for the same `req.ID` (e.g. node quorum reached just as the 5-second cleanup ticker fires the 30-second timeout), the loser's `SendResponse` call returns an error, and per the code above `h.activeRequests[req.ID]` is never deleted — it remains permanently, keyed by the caller-supplied `req.ID` (validated only for non-empty and ≤200 chars in `HandleJSONRPCUserMessage` [5](#0-4) , with uniqueness enforced only among currently-active IDs in `newActiveRequest` [6](#0-5) ).

Note `removeExpiredRequests` itself only builds its expired list under `h.mu.RLock()` and calls `sendResponse` outside the lock, so an entry that is concurrently completed by `HandleNodeMessage` between the scan and the timeout `sendResponse` call is exactly the race that produces the dangling entry.

### Impact Explanation
Each leaked entry holds a `*activeRequest` (embedded request struct, response map, callback channel) that is never freed, matching Chainlink's memory-exhaustion / unbounded resource consumption bounty class. Because `req.ID` is caller-controlled and each ID can only be "consumed" for one live request at a time (duplicate active IDs are rejected), an attacker must generate many distinct IDs and win the race repeatedly to accumulate leaked entries over time, giving slow but real unbounded growth reachable by any authorized-but-unprivileged vault user.

### Likelihood Explanation
Exploitability depends on winning a timing race between node quorum completion and the 5-second timeout sweep for the same request, which is not fully attacker-controlled (network/DON response timing is involved) but can be nudged (e.g., submitting requests expected to complete near the 30s `requestTimeout` boundary, or exploiting node response jitter). This makes the bug real but only probabilistically/iteratively exploitable rather than trivially deterministic per request; a persistent attacker automating many requests near the timeout boundary can realize a slow leak over time.

### Recommendation
Ensure `h.activeRequests` entry removal is unconditional and independent of whether `SendResponse` succeeds — e.g. always `delete(h.activeRequests, userRequest.req.ID)` (via `defer` or moving the delete before/regardless of the `SendResponse` error check), since the `req.ID` slot is no longer needed once any terminal `sendResponse` attempt has been made for it, not only successful ones.

### Proof of Concept
Go unit test plan in `core/services/gateway/handlers/vault` package:
1. Construct a `handler` with a stub `activeRequest` whose embedded `Callback` is a fake that returns an error from `SendResponse` unconditionally (simulating the "already sent" race loser).
2. Manually insert the `activeRequest` into `h.activeRequests["race-id"]`.
3. Call `h.sendResponse(ctx, ar, somePayload)` and assert it returns an error (confirming the failure path is hit).
4. Assert `h.activeRequests["race-id"]` is still present (`_, ok := h.activeRequests["race-id"]; assert ok == true`), demonstrating the leak.
5. As a regression test after fix, assert the entry is deleted (`ok == false`) even on the `SendResponse` error path.

### Citations

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

**File:** core/services/gateway/handlers/vault/handler.go (L403-410)
```go
func (h *handler) HandleJSONRPCUserMessage(ctx context.Context, req jsonrpc.Request[json.RawMessage], callback gwhandlers.Callback) error {
	if req.ID == "" {
		return errors.New("request ID cannot be empty")
	}
	if len(req.ID) > 200 {
		// Arbitrary limit to prevent abuse
		return errors.New("request ID is too long: " + strconv.Itoa(len(req.ID)) + ". max is 200 characters")
	}
```

**File:** core/services/gateway/handlers/vault/handler.go (L466-481)
```go
func (h *handler) newActiveRequest(req jsonrpc.Request[json.RawMessage], callback gwhandlers.Callback) (*activeRequest, error) {
	h.mu.Lock()
	defer h.mu.Unlock()
	if h.activeRequests[req.ID] != nil {
		h.lggr.Errorw("request id already exists", "requestID", req.ID)
		return nil, errors.New("request ID already exists: " + req.ID)
	}
	ar := &activeRequest{
		Callback:  callback,
		req:       req,
		createdAt: h.clock.Now(),
		responses: map[string]*jsonrpc.Response[json.RawMessage]{},
	}
	h.activeRequests[req.ID] = ar
	return ar, nil
}
```

**File:** core/services/gateway/handlers/vault/handler.go (L513-530)
```go
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

**File:** core/services/gateway/handlers/vault/handler.go (L797-833)
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

	h.mu.Lock()
	defer h.mu.Unlock()
	delete(h.activeRequests, userRequest.req.ID)
	h.lggr.Debugw("response sent to user", "requestID", userRequest.req.ID, "errorCode", resp.ErrorCode)
	return nil
```

**File:** core/services/gateway/handlers/common/callback.go (L18-26)
```go
func (c *Callback) SendResponse(payload handlers.UserCallbackPayload) error {
	if !c.sent.CompareAndSwap(false, true) {
		return errors.New("response already sent: each callback can only be used once")
	}
	// The channel is initialized with a buffer size of 1,
	// so this send will not block.
	c.ch <- payload
	return nil
}
```
