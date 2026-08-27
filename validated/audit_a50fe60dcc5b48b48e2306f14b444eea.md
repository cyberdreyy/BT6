### Title
Cross-user JSON-RPC request-ID collision lets an unprivileged client grief/DoS another user's `confidentialrelay` gateway request - (File: core/services/gateway/handlers/confidentialrelay/handler.go)

### Summary
The Tempus finding is a griefing bug where an attacker forces a strict, state-dependent check (`assert(yieldShares.balanceOf(address(this)) == 0)`) to fail by polluting shared contract state before the victim's transaction executes. The analogous pattern in `chainlink--010`'s confidential-relay gateway handler is a strict *uniqueness* check on a client-supplied, unvalidated key (`req.ID`) in a *shared, global map* (`activeRequests`) that is not scoped to the caller's identity, DON, or workflow. An unprivileged caller who can influence or guess another user's request ID can pre-insert an entry with that ID, causing the victim's legitimate request to be rejected.

### Finding Description
`HandleJSONRPCUserMessage` only validates that `req.ID` is non-empty and ≤200 characters: [1](#0-0) 

It then calls `newActiveRequest`, which enforces global uniqueness of `req.ID` across *all* callers of this handler instance (i.e., the whole DON's confidential-relay gateway handler), keyed purely by the client-controlled `req.ID` string with no sender/user/workflow partitioning: [2](#0-1) 

If an entry with the same ID already exists, the function returns an error and the request is rejected outright — this is functionally the same "strict state check that any other actor can pollute first" pattern that failed in the Tempus `assert` bug. Unlike `common.requestcache.go`'s `RequestCache`, which partitions pending requests by a `(sender, id)` tuple to prevent exactly this cross-user collision: [3](#0-2) 

the confidential-relay handler's `activeRequests` map has no such sender-scoping — it is keyed solely by `req.ID`.

### Impact Explanation
If an attacker can predict or intercept (e.g., front-run at the gateway ingress) another legitimate caller's `req.ID` before it reaches `newActiveRequest`, they can submit their own request with the identical ID first. The victim's subsequent legitimate call to `HandleJSONRPCUserMessage` will then fail with `"request ID already exists"`, denying that specific request (a request-level DoS, analogous to `depositAndFix` being blocked). This does not by itself cause direct fund loss or authentication bypass, so its severity is bounded to griefing/DoS of a targeted request rather than a bypass of authentication, secret disclosure, or fund movement.

### Likelihood Explanation
This requires that: (1) `req.ID` is either predictable, reused, or observable by another unprivileged party before the victim's request reaches the handler, and (2) an attacker has any legitimate access to submit JSON-RPC messages to the same DON's confidential-relay handler (which is required to reach this code path at all). Whether request IDs are attacker-visible/predictable in the current caller pathway to this handler could not be fully confirmed — the ID appears to be generated/consumed higher up in the gateway pipeline, and I did not find code that traces exactly how workflow/user callers choose or receive their `req.ID` before this handler is invoked. Given the index's coverage limits, this could not be verified with full confidence; a Devin session with full repo access would be needed to trace the exact ID-assignment call sites (e.g., callers of `HandleJSONRPCUserMessage`) to confirm attacker control/predictability of victim IDs.

### Recommendation
Scope `activeRequests` uniqueness to `(sender/caller identity, req.ID)` rather than `req.ID` alone, mirroring the `(sender, id)` keying already used in `core/services/gateway/handlers/common/requestcache.go`. This removes the shared global namespace that allows one caller to collide with another caller's request ID.

### Proof of Concept
Could not be fully constructed/verified: this requires confirming, from the entry points that call `HandleJSONRPCUserMessage`/`HandleGatewayMessage` for the confidential relay handler (in `gateway.go` / `multihandler.go`), whether `req.ID` is attacker-observable or attacker-choosable by a different unprivileged caller than the intended requester before the victim's call reaches `newActiveRequest`. This trace was not completed within the available search depth.

### Citations

**File:** core/services/gateway/handlers/confidentialrelay/handler.go (L349-366)
```go
func (h *handler) HandleJSONRPCUserMessage(ctx context.Context, req jsonrpc.Request[json.RawMessage], callback gwhandlers.Callback) error {
	if req.ID == "" {
		return errors.New("request ID cannot be empty")
	}
	if len(req.ID) > 200 {
		return errors.New("request ID is too long: " + strconv.Itoa(len(req.ID)) + ". max is 200 characters")
	}

	l := logger.With(h.lggr, "method", req.Method, "requestID", req.ID)
	l.Debugw("handling confidential relay request")

	ar, err := h.newActiveRequest(req, callback)
	if err != nil {
		return err
	}

	return h.fanOutToNodes(ctx, l, ar)
}
```

**File:** core/services/gateway/handlers/confidentialrelay/handler.go (L368-383)
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
