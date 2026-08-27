### Title
Unauthenticated `secrets_publicKeyGet` requests to the Vault Gateway handler allow unbounded `activeRequests` memory growth and DON-wide request amplification - (File: core/services/gateway/handlers/vault/handler.go)

### Summary
The Vault gateway handler's `HandleJSONRPCUserMessage` accepts `MethodPublicKeyGet` requests from any unprivileged client without authorization, and creates an entry in the unbounded `activeRequests` map for every distinct request ID before fanning the request out to every DON member node. There is no cap on the number of concurrent `activeRequests`, and no per-sender/global rate limiter on the user-facing ingestion path (only the responses coming back *from* nodes are rate-limited). This mirrors the reported bug class of unrestricted, cheaply-created queued/pending entries causing resource exhaustion and processing delays for legitimate users.

### Finding Description
`HandleJSONRPCUserMessage` special-cases `vaulttypes.MethodPublicKeyGet` to skip authorization entirely ("Public key requests don't require authorization") [1](#0-0) . When the public key isn't cached, it calls `h.newActiveRequest(req, callback)`, which only rejects a request if its exact ID already exists — there is no limit on the total size of the map [2](#0-1) . The request struct only requires `req.ID` be non-empty and ≤200 chars [3](#0-2) , both trivially satisfiable by an attacker generating unique IDs (e.g., UUIDs) per request.

Each accepted request is then fanned out via `fanOutToVaultNodes`, which sends the request to every member of the DON [4](#0-3) , amplifying a single cheap unauthenticated client request into N node-directed messages.

Entries are only cleared by `removeExpiredRequests`, which is presumably invoked on a periodic timer/ticker and removes requests older than `requestTimeout` (default 30s, or `RequestTimeoutSec-1` per gateway job defaults, e.g. 13-29s) [5](#0-4) . This is directly analogous to the reported bug class: an unprivileged actor can cheaply enqueue an unbounded number of pending entries (here, `activeRequests`) that the system must service/expire before it can be flushed, and the only bound is a fixed timeout window rather than a hard cap on outstanding entries.

By contrast, other request tracking structures in the same codebase area explicitly guard against this: `RequestCache` in `handlers/common/requestcache.go` enforces a `maxCacheSize` and returns `"request cache is full"` once exceeded [6](#0-5) , and the confidential relay handler's `newActiveRequest` has the identical missing-cap pattern [7](#0-6) , suggesting this is a systemic gap rather than an isolated oversight, but the Vault handler is the most directly reachable instance because `MethodPublicKeyGet` explicitly bypasses authorization.

I was not able to fully confirm the exact refresh interval/cadence at which `removeExpiredRequests` is invoked (i.e., whether it runs on every tick of a fixed-interval ticker or is otherwise bounded), since the `Start()` method body was not retrieved before the tool budget was exhausted — this is a gap in verification, not a claim about the code's behavior.

### Impact Explanation
An unauthenticated client (any actor that can reach the gateway's user-facing JSON-RPC endpoint) can generate an effectively unbounded number of unique `secrets_publicKeyGet` requests within the timeout window, each of which:
1. Consumes memory in the gateway's `activeRequests` map (proportional to attacker throughput, since there's no size cap), and
2. Triggers a fan-out message to every node in the Vault DON.

This can cause elevated memory usage/GC pressure on the gateway node and unnecessary load/traffic on every Vault DON member, degrading availability and delaying processing of legitimate vault requests (secrets create/update/delete/list) which share the same `activeRequests` map and DON fan-out path.

### Likelihood Explanation
Likelihood is moderate-to-low in practice: the underlying network layer (gateway user server) may impose connection/read-timeout limits, and the `removeExpiredRequests` mechanism bounds the maximum age of any single entry. However, since there is no explicit rate limiter or cap gating the creation of new `activeRequests` entries specifically for user-originated `MethodPublicKeyGet` traffic (the only rate limiter present, `nodeRateLimiter`, gates *responses from nodes*, not intake from users), an attacker with modest bandwidth can sustain enough throughput to keep the map persistently large and keep pushing fan-out traffic to the DON, similar in spirit to the reported griefing pattern (cheap, repeatable, unauthenticated queue entries that the system must continually service).

### Recommendation
- Add a hard upper bound (`maxActiveRequests`) to the Vault handler's `activeRequests` map, mirroring the `maxCacheSize` pattern already used in `handlers/common/requestcache.go`, and reject/rate-limit new requests once the cap is reached.
- Apply a per-sender/global rate limiter on the *incoming* user-facing path (`HandleJSONRPCUserMessage`), not just on node response ingestion, particularly for the unauthenticated `MethodPublicKeyGet` path.
- Consider requiring at least a lightweight anti-abuse check (e.g., connection-level throttling) before allowing any request — even an unauthenticated one — to trigger a DON-wide fan-out.
- Apply the same fix to the analogous `confidentialrelay` handler's `activeRequests` map, which has the same unbounded-growth pattern.

### Proof of Concept
1. An attacker connects to the gateway's user-facing HTTP/JSON-RPC endpoint (no credentials required for `secrets_publicKeyGet`).
2. The attacker repeatedly sends JSON-RPC requests with `method: "secrets_publicKeyGet"` and a freshly generated unique `id` (e.g., UUID) for each request, faster than the `requestTimeout` window allows entries to expire.
3. Each request passes the `req.ID` non-empty/≤200-char check [8](#0-7) , bypasses authorization [1](#0-0) , and is inserted into `h.activeRequests` with no size check [2](#0-1) .
4. Each insertion also triggers `fanOutToVaultNodes`, sending the request to every DON member [4](#0-3) .
5. Sustained at sufficient rate, this grows `activeRequests` unboundedly (bounded only by attacker throughput and the timeout window) and generates continuous DON-wide fan-out traffic, exactly matching the reported bug class of cheap, unauthenticated, unbounded pending-request accumulation.

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

**File:** core/services/gateway/handlers/vault/handler.go (L413-429)
```go
	if req.Method == vaulttypes.MethodPublicKeyGet {
		// Public key requests don't require authorization,
		// Let's process this request right away.
		// Note we cache this value quite aggressively so don't need to worry about DoS.
		publicKeyResponseBytes, cachedPublicKey := h.getCachedPublicKey()
		if cachedPublicKey == nil {
			// Not found in cache. Fetch from nodes.
			ar, err := h.newActiveRequest(req, callback)
			if err != nil {
				h.lggr.Errorw("failed to create new activeRequest", "error", err)
				return err
			}
			return h.handlePublicKeyGet(ctx, ar)
		}
		h.lggr.Debugw("returning cached public key response")
		return h.handlePublicKeyGetSynchronously(ctx, req, publicKeyResponseBytes, callback)
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

**File:** core/services/gateway/handlers/vault/handler.go (L726-742)
```go
func (h *handler) fanOutToVaultNodes(ctx context.Context, l logger.Logger, ar *activeRequest) error {
	var nodeErrors []error
	for _, node := range h.donConfig.Members {
		err := h.don.SendToNode(ctx, node.Address, &ar.req)
		if err != nil {
			nodeErrors = append(nodeErrors, err)
			l.Errorw("error sending request to node", "node", node.Address, "error", err)
		}
	}

	if len(nodeErrors) == len(h.donConfig.Members) && len(nodeErrors) > 0 {
		return h.sendResponse(ctx, ar, h.errorResponse(ar.req, api.FatalError, errors.New("failed to forward user request to nodes"), nil))
	}

	l.Debugw("successfully forwarded request to Vault nodes")
	return nil
}
```

**File:** core/services/gateway/handlers/common/requestcache.go (L46-66)
```go
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
	if len(c.cache) >= int(c.maxCacheSize) {
		return errors.New("request cache is full")
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
