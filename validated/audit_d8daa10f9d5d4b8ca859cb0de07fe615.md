### Title
Client-controlled `MessageId` collision in the legacy Gateway WebAPI handler allows cross-user response confusion / griefing - ([File: core/services/gateway/handlers/capabilities/handler.go])

### Summary
The TrueFi report describes a griefing attack where an unprivileged-but-eligible actor races a victim's transaction to mutate shared state (pool liquidity) right before the victim's operation reads it, degrading the victim's outcome. The analogous pattern in this codebase is the legacy Gateway WebAPI trigger path, where the map key used to route an asynchronous node response back to the correct user callback is the fully attacker-controlled `MessageId` field, with no uniqueness/conflict check, unlike the newer v2 trigger handler which explicitly rejects ID collisions.

### Finding Description
In `core/services/gateway/handlers/capabilities/handler.go`, `HandleLegacyUserMessage` stores the caller's callback keyed only by `msg.Body.MessageId`: [1](#0-0) 

`MessageId` originates entirely from the inbound HTTP JSON body decoded by the gateway (`api.MessageBody.MessageId`), which is validated only for length/format, not uniqueness or ownership: [2](#0-1) 

Any unauthenticated/unprivileged client hitting the internet-facing gateway (`gateway.ProcessRequest` → `HandleLegacyUserMessage`) can pick an arbitrary `MessageId`. Because `savedCallbacks` is a plain map write with no existence check, an attacker who submits a second request with the same `MessageId` as a concurrent victim request silently **overwrites** the victim's saved callback entry: [3](#0-2) 

When a DON node later responds, `handleWebAPITriggerMessage` looks up and deletes the callback purely by `MessageId` and delivers the response to whatever callback currently occupies that slot: [4](#0-3) 

This is structurally the same bug class as the TrueFi report: a piece of shared, externally-influenceable state (there: pool liquidity; here: the `savedCallbacks` map slot) is mutated by an unprivileged actor in a race window right before it is consumed, corrupting the outcome for another legitimate user. Here the effect is response routing corruption rather than a fee calculation, but the root cause — an attacker-controlled key racing a victim's in-flight request against unprotected shared state — is the same TOCTOU/griefing pattern.

By contrast, the newer v2 HTTP trigger handler (`core/services/gateway/handlers/capabilities/v2/http_trigger_handler.go`) explicitly detects and rejects duplicate/in-flight request IDs before registering a callback, confirmed by test `TestHttpTriggerHandler_HandleUserTriggerRequest/duplicate_request_ID`: [5](#0-4) 
This demonstrates the maintainers are aware such collisions must be rejected — a protection the legacy path (`handler.go`) lacks entirely.

### Impact Explanation
If an attacker wins the race and overwrites `savedCallbacks[X]`:
- The victim's original callback is orphaned; the victim's HTTP request will hang until pruned (`pruneCallbacks`, ~2 minutes default) or time out — a denial-of-service/griefing effect against a specific unprivileged user, directly analogous to Sherlock users incurring degraded outcomes from Alice's TrueFi front-running.
- Depending on timing, the response destined for the victim's request (matched only by `MessageId`, not by requester identity or signature) can instead be delivered to the attacker's callback, and vice versa — a cross-user response confusion where the attacker may receive a response payload intended for another user's trigger execution.

This is a legitimate, unprivileged-actor-reachable vulnerability class match per the validation criteria (cross-user response confusion via allowlist/id collision on an internet-facing gateway handler).

### Likelihood Explanation
Exploitability requires only sending two HTTP requests to the public gateway endpoint with an identical, attacker-chosen `MessageId`, timed to race a legitimate victim request whose ID the attacker can predict, brute-force, or intercept (e.g., if IDs are low-entropy, sequential, or reused by client tooling such as the sample scripts in `core/scripts/gateway/web_api_trigger/invoke_trigger.go`, which use a fixed default `"12345"`). No special role, credit score, or whitelist step is needed (unlike TrueFi's borrower-whitelisting), making this arguably easier to mount than the original report's precondition.

### Recommendation
- In `HandleLegacyUserMessage`, check for an existing entry in `savedCallbacks` for the given `MessageId` before inserting, and reject the new request (mirroring the v2 handler's "in-flight request" rejection) rather than silently overwriting.
- Scope callback keys to include the request signer/sender address (already available via `msg.Body.Sender` after `Validate()`) in addition to `MessageId`, so collisions between different senders cannot occur.
- Consider deprecating/migrating remaining users of the legacy path to the v2 `httpTriggerHandler`, which already implements per-ID conflict detection.

### Proof of Concept
1. Victim sends a legacy `web_api_trigger` request to the gateway with `MessageId = "X"`; `HandleLegacyUserMessage` stores `savedCallbacks["X"] = victimCallback` and forwards the request to DON nodes.
2. Before any node responds, attacker sends their own signed legacy request also using `MessageId = "X"` (attacker only needs a valid keypair to sign; no special role or allowlisting required). `HandleLegacyUserMessage` overwrites `savedCallbacks["X"] = attackerCallback`.
3. A DON node responds to the victim's original request with `MessageId = "X"`. `handleWebAPITriggerMessage` looks up `savedCallbacks["X"]`, finds the attacker's callback, deletes the entry, and delivers the victim's response payload to the attacker.
4. The victim's HTTP request never receives a response and eventually times out — demonstrating both response hijacking and denial-of-service against the victim, analogous to the TrueFi griefing pattern where an unprivileged actor races and corrupts shared state to degrade another user's outcome.

### Citations

**File:** core/services/gateway/handlers/capabilities/handler.go (L48-61)
```go
type handler struct {
	services.StateMachine
	config          HandlerConfig
	don             handlers.DON
	donConfig       *config.DONConfig
	savedCallbacks  map[string]*savedCallback
	mu              sync.Mutex
	lggr            logger.Logger
	httpClient      network.HTTPClient
	nodeRateLimiter *ratelimit.RateLimiter
	wg              sync.WaitGroup
	stopCh          services.StopChan
	metrics         *metrics
}
```

**File:** core/services/gateway/handlers/capabilities/handler.go (L148-162)
```go
func (h *handler) handleWebAPITriggerMessage(ctx context.Context, msg *api.Message, nodeAddr string) error {
	h.mu.Lock()
	savedCb, found := h.savedCallbacks[msg.Body.MessageId]
	delete(h.savedCallbacks, msg.Body.MessageId)
	h.mu.Unlock()

	if found {
		// Send first response from a node back to the user, ignore any other ones.
		// TODO: in practice, we should wait for at least 2F+1 nodes to respond and then return an aggregated response
		// back to the user.
		codec := api.JsonRPCCodec{}
		return savedCb.SendResponse(handlers.UserCallbackPayload{RawResponse: codec.EncodeLegacyResponse(msg), ErrorCode: api.NoError})
	}
	return nil
}
```

**File:** core/services/gateway/handlers/capabilities/handler.go (L411-414)
```go
	h.mu.Lock()
	h.savedCallbacks[msg.Body.MessageId] = &savedCallback{id: msg.Body.MessageId, createdAt: time.Now(), Callback: callback}
	don := h.don
	h.mu.Unlock()
```

**File:** core/services/gateway/api/message.go (L42-66)
```go
type MessageBody struct {
	MessageId string `json:"message_id"`
	Method    string `json:"method"`
	DonId     string `json:"don_id"`
	Receiver  string `json:"receiver"`
	// Service-specific payload, decoded inside the Handler.
	Payload json.RawMessage `json:"payload,omitempty"`

	// Fields only used locally for convenience. Not serialized.
	Sender string `json:"-"`
}

func (m *Message) Validate() error {
	if m == nil {
		return errors.New("nil message")
	}
	if len(m.Signature) != MessageSignatureHexEncodedLen {
		return errors.New("invalid hex-encoded signature length")
	}
	if len(m.Body.MessageId) == 0 || len(m.Body.MessageId) > MessageIdMaxLen {
		return errors.New("invalid message ID length")
	}
	if strings.HasSuffix(m.Body.MessageId, NullChar) {
		return errors.New("message ID ending with null bytes")
	}
```

**File:** core/services/gateway/handlers/capabilities/v2/http_trigger_handler_test.go (L317-355)
```go
	t.Run("duplicate request ID", func(t *testing.T) {
		handler, mockDon := createTestTriggerHandler(t)
		privateKey := createTestPrivateKey(t)
		registerWorkflow(t, handler, workflowID, privateKey)
		callback1 := hc.NewCallback()
		callback2 := hc.NewCallback()

		triggerReq := gateway_common.HTTPTriggerRequest{
			Workflow: gateway_common.WorkflowSelector{
				WorkflowID: workflowID,
			},
			Input: []byte(`{"key": "value"}`),
		}
		reqBytes, err := json.Marshal(triggerReq)
		require.NoError(t, err)

		rawParams := json.RawMessage(reqBytes)
		req := &jsonrpc.Request[json.RawMessage]{
			Version: "2.0",
			ID:      requestID,
			Method:  gateway_common.MethodWorkflowExecute,
			Params:  &rawParams,
		}
		// First request should succeed
		req.Auth = createTestJWTToken(t, req, privateKey)
		mockDon.EXPECT().SendToNode(mock.Anything, mock.Anything, mock.Anything).Return(nil).Times(3)
		err = handler.HandleUserTriggerRequest(t.Context(), req, callback1, time.Now())
		require.NoError(t, err)

		// Second request with same ID should fail
		req.Auth = createTestJWTToken(t, req, privateKey)
		err = handler.HandleUserTriggerRequest(t.Context(), req, callback2, time.Now())
		require.Error(t, err)
		require.Contains(t, err.Error(), "in-flight request")

		r, err := callback2.Wait(t.Context())
		require.NoError(t, err)
		requireUserErrorSent(t, r, jsonrpc.ErrConflict)
	})
```
