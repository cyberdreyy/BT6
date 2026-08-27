### Title
Global, unscoped `requestID` collision in `httpTriggerHandler` enables cross-workflow Denial of Service via ID front-running - (File: core/services/gateway/handlers/capabilities/v2/http_trigger_handler.go)

### Summary
The gateway's HTTP-trigger handler registers pending workflow-execution requests in a single map keyed only by the client-supplied `requestID`, with no scoping by workflow, owner, or sender. Any unprivileged, authenticated caller can pick an arbitrary `requestID` (subject only to non-empty and "no `/`" checks) and race to occupy that key, causing a completely unrelated legitimate caller's request using the same ID to be rejected with a conflict error — the same root-cause pattern as the reported Folks Finance `loanId` front-running bug (attacker-chosen identifier squats a shared namespace before the victim, causing the victim's otherwise-valid request to revert).

### Finding Description
`HandleUserTriggerRequest` processes a trigger request end-to-end and, near the end of the pipeline, calls `setupCallback(ctx, req.ID, ...)`: [1](#0-0) 

`setupCallback` stores the pending request in `h.callbacks`, a map declared as `map[string]savedCallback // requestID -> savedCallback`: [2](#0-1) 

The registration logic only checks whether the `requestID` key already exists — it does not scope the key by `workflowID`, workflow owner, or caller identity: [3](#0-2) 

The only validation applied to the attacker-controlled `requestID` is that it is non-empty and does not contain `/`: [4](#0-3) 

Because the JWT/authorization check (`authorizeRequest`) only verifies that the caller is authorized for **their own** workflow — it does not bind or validate the `requestID` value itself — any authorized caller for *any* workflow can submit a request with a `requestID` string equal to one another caller (for a different workflow/owner) is about to use or has just used. If the attacker's request reaches `setupCallback` first, the legitimate caller's later request with the same `requestID` is rejected: [5](#0-4) 

This is confirmed by the existing test demonstrating that a second request with the same `requestID` fails outright with a JSON-RPC conflict error, even though this test only exercises the same-workflow case: [6](#0-5) 

This mirrors the reported bug class exactly: a shared identifier namespace with an "already exists → reject" check, where the identifier is fully caller-controlled and not scoped to the caller/session, enabling one unprivileged actor to squat an identifier and deny service to another unprivileged actor who independently chooses (or is assigned/generates) the same identifier. Unlike the safer pattern used elsewhere in the gateway — e.g. `common.RequestCache.NewRequest`, which scopes the dedup key by `globalId{sender, id}` — the `httpTriggerHandler.callbacks` map has no sender/workflow scoping at all: [7](#0-6) 

### Impact Explanation
An attacker with a valid JWT for their own (possibly unrelated) workflow can cause requests from a *different* workflow/owner to fail with `jsonrpc.ErrConflict` ("requestID ... has already been used") simply by using the same `requestID` string. Since `requestID` values are entirely user-chosen and unconstrained beyond basic format checks, common/predictable ID patterns (sequential counters, timestamps, fixed idempotency keys used by client SDKs, or IDs observed from public logs/traces) make collisions realistic to engineer deliberately. This is a griefing/DoS vector: the victim's legitimate workflow-trigger request is denied service, they must retry with a different ID, and any external system relaying the trigger (analogous to the "bridge fee" in the original report) incurs wasted cost/latency. Because the map is global across the gateway node rather than scoped per workflow/owner, a single attacker can broadly and repeatedly grief many unrelated tenants sharing the same DON/gateway.

### Likelihood Explanation
Likelihood is moderate: exploitation requires the attacker to either predict or observe a victim's `requestID` before the victim's request is processed and to win the race by submitting first. Since `requestID` is not required to be random/unique per caller (only non-empty, no `/`), and many client integrations may use predictable or low-entropy identifiers (timestamps, incrementing counters, static keys), an attacker could proactively pre-register a broad set of likely IDs, or observe them from client-side logs/telemetry, to increase the probability of a collision with a targeted victim.

### Recommendation
Scope the `callbacks` map key by `(workflowID, requestID)` or `(workflowOwner, requestID)` instead of `requestID` alone, mirroring the sender-scoped design already used in `common.RequestCache` (`globalId{sender, id}`). This eliminates cross-tenant ID collisions while preserving the existing per-workflow duplicate-request protection.

### Proof of Concept
1. Attacker obtains a valid JWT authorizing them for `workflowB` (owned by attacker).
2. Attacker sends `HandleUserTriggerRequest` with `req.ID = "shared-id-123"` for `workflowB`; this succeeds and calls `setupCallback(ctx, "shared-id-123", ...)`, inserting `h.callbacks["shared-id-123"]`.
3. Victim, unaware of the attacker, independently sends a legitimate trigger request for `workflowA` (different owner) also using `req.ID = "shared-id-123"` (e.g., because their client library generates predictable/reused idempotency keys).
4. Victim's `setupCallback` call finds `h.callbacks["shared-id-123"]` already present (from step 2) and returns a conflict error via `h.handleUserError(ctx, requestID, jsonrpc.ErrConflict, ...)`, denying the victim's otherwise valid request for an entirely unrelated workflow.

### Citations

**File:** core/services/gateway/handlers/capabilities/v2/http_trigger_handler.go (L53-66)
```go
type httpTriggerHandler struct {
	services.StateMachine
	config                  ServiceConfig
	shards                  []*shardEndpoint
	nodeAddrToShard         map[string]*shardEndpoint
	lggr                    logger.Logger
	callbacksMu             sync.Mutex
	callbacks               map[string]savedCallback // requestID -> savedCallback
	stopCh                  services.StopChan
	workflowMetadataHandler *WorkflowMetadataHandler
	userRateLimiter         limits.RateLimiter
	metrics                 *metrics.Metrics
	wg                      sync.WaitGroup
}
```

**File:** core/services/gateway/handlers/capabilities/v2/http_trigger_handler.go (L134-140)
```go
	doneCh, err := h.setupCallback(ctx, req.ID, callback, requestStartTime, workflowID)
	if err != nil {
		return err
	}

	return h.sendWithRetries(ctx, legacyExecutionID, executionIDWithTriggerIndex, reqWithKey, workflowID, doneCh)
}
```

**File:** core/services/gateway/handlers/capabilities/v2/http_trigger_handler.go (L183-195)
```go
func (h *httpTriggerHandler) validateRequestID(ctx context.Context, requestID string, callback handlers.Callback) error {
	if requestID == "" {
		h.handleUserError(ctx, requestID, jsonrpc.ErrInvalidRequest, "'id' field is required and cannot be empty. Use a new unique request 'id' for each request", callback)
		return errors.New("empty request ID")
	}
	// Request IDs from users must not contain "/", since this character is reserved
	// for internal node-to-node message routing (e.g., "http_action/{workflowID}/{uuid}").
	if strings.Contains(requestID, "/") {
		h.handleUserError(ctx, requestID, jsonrpc.ErrInvalidRequest, "request ID must not contain '/'", callback)
		return errors.New("request ID must not contain '/'")
	}
	return nil
}
```

**File:** core/services/gateway/handlers/capabilities/v2/http_trigger_handler.go (L398-406)
```go
func (h *httpTriggerHandler) setupCallback(ctx context.Context, requestID string, callback handlers.Callback, requestStartTime time.Time, workflowID string) (<-chan struct{}, error) {
	h.callbacksMu.Lock()
	defer h.callbacksMu.Unlock()

	if _, found := h.callbacks[requestID]; found {
		h.handleUserError(ctx, requestID, jsonrpc.ErrConflict, fmt.Sprintf("requestID: %s has already been used. Ensure the requestID is unique for each request.", requestID), callback)
		return nil, fmt.Errorf("in-flight request ID: %s", requestID)
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

**File:** core/services/gateway/handlers/common/requestcache.go (L34-63)
```go
type globalId struct {
	sender string
	id     string
}

type pendingRequest[T any] struct {
	handlers.Callback
	responseData *T
	timeoutTimer *time.Timer
	mu           sync.Mutex
}

func NewRequestCache[T any](timeout time.Duration, maxCacheSize uint32) RequestCache[T] {
	return &requestCache[T]{cache: make(map[globalId]*pendingRequest[T]), timeout: timeout, maxCacheSize: maxCacheSize}
}

func (c *requestCache[T]) NewRequest(lggr logger.Logger, request *api.Message, callback handlers.Callback, responseData *T) error {
	if request == nil {
		return errors.New("request is nil")
	}
	if responseData == nil {
		return errors.New("responseData is nil")
	}
	key := globalId{request.Body.Sender, request.Body.MessageId}
	c.mu.Lock()
	defer c.mu.Unlock()
	_, ok := c.cache[key]
	if ok {
		return errors.New("request already exists")
	}
```
