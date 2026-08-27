### Title
Global, user-controlled `requestID` key allows one authenticated workflow caller to block another user's HTTP trigger request - (File: core/services/gateway/handlers/capabilities/v2/http_trigger_handler.go)

### Summary
The `httpTriggerHandler` accepts a client-supplied `requestID` (`req.ID`) and uses it, without any collision-resistant derivation, as the sole key into a single map shared by **all** workflows and all callers. The only validation performed is that the ID is non-empty and does not contain `/`. Because the key space is global rather than scoped per workflow/owner, an unrelated authenticated caller can pre-occupy a `requestID` value that another legitimate user is about to use, causing that user's genuine trigger request to be rejected with a conflict error — the same "attacker squats a shared uniqueness key that a victim needs" bug class described in the external report (there the key was an unbonding tx hash; here it is the JSON-RPC `requestID`).

### Finding Description
`validateRequestID` only rejects empty IDs or IDs containing `/`; it performs no uniqueness or ownership binding: [1](#0-0) 

The actual uniqueness check happens later in `setupCallback`, which locks a single mutex and looks up the ID in `h.callbacks`, a map keyed **only** by `requestID` with no workflow or owner component in the key: [2](#0-1) 

The map itself is declared as global state for the handler instance, shared across every workflow the gateway serves: [3](#0-2) 

If a second caller submits a request with a `requestID` that is currently in-flight for a *different* workflow/owner, `setupCallback` rejects it with `jsonrpc.ErrConflict`: [4](#0-3) 

This mirrors the external report's root cause exactly: a value that should be unique *per legitimate owner of an action* is instead accepted from any caller and stored in a shared keyspace with a naive existence check, so a malicious but otherwise properly authenticated caller (authorized against their own, unrelated workflow) can occupy another user's key first and deny them service.

### Impact Explanation
An authenticated caller of one workflow can block (deny) a specific in-flight trigger request of a caller of a completely different, unrelated workflow simply by using the same `requestID` string first. Because `HandleUserTriggerRequest` performs authorization (`authorizeRequest`) before checking `requestID` uniqueness, the interfering caller does not need any privilege relative to the victim's workflow — they only need to be an authorized caller of *some* workflow served by the same gateway instance, and to guess/know the `requestID` the victim will send. This is a cross-user denial-of-service on the internet-facing gateway trigger path, matching the "unauthorized job run"/"cross-user response confusion" impact classes (here manifesting as blocked legitimate execution rather than misrouted execution, since the duplicate insert is atomically rejected).

### Likelihood Explanation
Exploitation requires the attacker to predict or learn the exact `requestID` a victim will use before the victim's request lands. In practice `requestID`s are typically client-chosen UUIDs or sequence numbers; if a client uses predictable IDs (timestamps, incrementing counters, or IDs exposed in logs/other observable channels) an attacker can win the race deterministically. Likelihood is lower than the original report (where the colliding value — a Bitcoin tx hash — could be computed deterministically from public data before submission), because here the value is arbitrary and caller-chosen rather than derived from public/predictable data. Still, the missing scoping of the uniqueness check to (workflowID, requestID) rather than a global requestID is a genuine, unprivileged, reachable defect.

### Recommendation
Scope the callback map key to include the workflow identity (e.g., `(workflowID, requestID)` or `(workflowOwner, workflowID, requestID)`) instead of `requestID` alone, so that one workflow's callers cannot collide with another's. Additionally, consider deriving/salting the internal tracking key (e.g., combining `requestID` with the authenticated key/owner) rather than trusting the raw client-supplied string as a global uniqueness token.

### Proof of Concept
1. Attacker (Eve) obtains valid auth for `workflowA` (any workflow they are authorized to call).
2. Eve learns or predicts the `requestID` value ("R1") that victim (Alice) will use for her own trigger request against `workflowB` (e.g., observed via shared tooling, predictable ID scheme, or timing).
3. Eve sends a `workflows.execute` request to the gateway with `id: "R1"`, `workflow: workflowA`, valid auth for workflowA. `setupCallback` inserts `h.callbacks["R1"]`.
4. Alice sends her legitimate request with `id: "R1"`, `workflow: workflowB`, valid auth for workflowB.
5. `setupCallback` finds `"R1"` already present and rejects Alice's request with `jsonrpc.ErrConflict` / "requestID: R1 has already been used", even though Alice's request is for an entirely unrelated workflow. [4](#0-3)

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

**File:** core/services/gateway/handlers/capabilities/v2/http_trigger_handler.go (L398-405)
```go
func (h *httpTriggerHandler) setupCallback(ctx context.Context, requestID string, callback handlers.Callback, requestStartTime time.Time, workflowID string) (<-chan struct{}, error) {
	h.callbacksMu.Lock()
	defer h.callbacksMu.Unlock()

	if _, found := h.callbacks[requestID]; found {
		h.handleUserError(ctx, requestID, jsonrpc.ErrConflict, fmt.Sprintf("requestID: %s has already been used. Ensure the requestID is unique for each request.", requestID), callback)
		return nil, fmt.Errorf("in-flight request ID: %s", requestID)
	}
```
