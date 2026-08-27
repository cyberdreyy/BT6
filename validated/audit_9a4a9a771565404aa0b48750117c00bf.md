### Title
Gateway fans out `MethodCapabilityExec` requests to an entire DON without verifying the caller owns/is authorized for the target workflow - ([File: core/services/gateway/handlers/confidentialrelay/handler.go])

### Summary
`handler.HandleJSONRPCUserMessage` in `core/services/gateway/handlers/confidentialrelay/handler.go` only validates that `req.ID` is non-empty and under 200 characters before calling `h.newActiveRequest` and `h.fanOutToNodes`, which broadcasts the raw request to every node in `h.donConfig.Members` via `h.don.SendToNode`. No check ties the request's caller identity to the workflow/job referenced inside the JSON-RPC `Params` payload, so any caller who can reach this method can cause a fan-out of a `MethodCapabilityExec` request naming a workflow they do not own.

### Finding Description
The request path is: `gateway.ProcessRequest` (`core/services/gateway/gateway.go:218`) decodes the raw HTTP body into a `jsonrpc2.Request`, resolves a handler purely by `ServiceName`/`DonId` (`core/services/gateway/gateway.go:235-262`), and dispatches into `multiHandler.HandleJSONRPCUserMessage` (`core/services/gateway/multihandler.go:62`), which looks the target handler up **only by JSON-RPC `Method` string** (`getHandler`, `multihandler.go:80`) and forwards the request verbatim.

Inside the confidentialrelay handler: [1](#0-0) 

The only gating logic is a request-ID uniqueness/length check performed in `newActiveRequest`: [2](#0-1) 

`fanOutToNodes` then unconditionally sends `ar.req` (the attacker-supplied JSON-RPC payload, which contains the target workflow/job identifiers in `Params`) to **every** node configured for that DON: [3](#0-2) 

At no point between the HTTP entrypoint (`gateway.ProcessRequest`), the method dispatch (`multiHandler.getHandler`), and the handler body (`HandleJSONRPCUserMessage` → `newActiveRequest` → `fanOutToNodes`) is there any lookup against a per-workflow or per-DON allowlist that binds the caller's identity to the specific workflow named in the request `Params`. `config.DONConfig.Members` (used for fan-out) represents the DON's *node* addresses, not an authorization list of permitted callers/workflow-owners, and nothing in this file cross-references it against the request payload's target workflow. This is an `AUTHORIZATION_EXACTNESS` gap: the enclave/DON is trusted to reject bad requests downstream, but the gateway itself performs zero workflow-ownership authorization before broadcasting the exec request to the whole DON's node set.

### Impact Explanation
Any caller capable of submitting a JSON-RPC request routed to this handler's service/method (an internet-facing, unauthenticated-by-this-code-path or merely "any signed request" caller per the threat model) can name an arbitrary workflow ID in the `MethodCapabilityExec` payload and force the gateway to broadcast a capability-execution request to all nodes of the DON, on behalf of a workflow it does not own. This matches Chainlink's "unauthorized job/workflow run trigger" bounty impact class — an attacker can trigger DON-wide capability execution attempts for a target job/workflow without being a member of that workflow's authorized allowlist.

### Likelihood Explanation
No special privilege is required beyond being able to reach the gateway's JSON-RPC user endpoint and construct a syntactically valid request with a unique `ID` and the `MethodCapabilityExec` method name; the only preconditions enforced are ID uniqueness/length (`core/services/gateway/handlers/confidentialrelay/handler.go:350-355,371-374`). The action is fully repeatable per unique request ID, and the resulting fan-out to `h.donConfig.Members` happens deterministically on every call. Whether an upstream signature check (in `jsonrpc2.DecodeRequest`, called from `gateway.ProcessRequest`) restricts *who* can sign requests could not be fully verified from the indexed code, but per the stated threat model, "any address sending signed gateway requests" is treated as an unprivileged attacker, and no code in this handler or its callers verifies that such a signer is authorized for the *specific* workflow embedded in the payload.

### Recommendation
Before calling `h.newActiveRequest`/`h.fanOutToNodes` in `HandleJSONRPCUserMessage`, extract the target workflow/job identifier from `req.Params`, resolve the workflow's owning DON and its authorized-caller allowlist, and verify the requesting identity (derived from the verified request signature) is a member of that allowlist. Reject with `api.InvalidParamsError`/an authorization error if the check fails, prior to any state mutation (`newActiveRequest`) or network fan-out (`fanOutToNodes`).

### Proof of Concept
Go handler-level test plan (extending `core/services/gateway/handlers/confidentialrelay/handler_test.go`):
1. Construct a `handler` via `NewHandler` with a `donConfig` for DON "A" and a mock `gwhandlers.DON` (`mocks.DON`) that records all `SendToNode` calls.
2. Build a `jsonrpc.Request[json.RawMessage]{Method: MethodCapabilityExec, ID: "attacker-1", Params: <payload naming workflowID belonging to a different, unrelated caller/DON>}`.
3. Call `handler.HandleJSONRPCUserMessage(ctx, req, callback)` directly (simulating an unprivileged caller with no relationship to the named workflow).
4. Assert:
   - No error is returned before fan-out (i.e., no ownership/allowlist check short-circuits the call).
   - The mock DON's `SendToNode` was invoked once per member in `donConfig.Members`, confirming the request was broadcast to the entire DON despite the caller not owning the target workflow.
   - Repeat with a second workflow ID also not owned by the caller — the fan-out succeeds identically, proving no per-workflow authorization gate exists in this code path.

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

**File:** core/services/gateway/handlers/confidentialrelay/handler.go (L618-652)
```go
func (h *handler) fanOutToNodes(ctx context.Context, l logger.Logger, ar *activeRequest) error {
	var (
		group      errgroup.Group
		nodeErrors atomic.Uint32
	)

	// Each send is bounded independently. A node whose websocket accepts no writes blocks
	// until its context is cancelled, and because the caller only reads the response callback
	// after this function returns, an unbounded send would hold the request open until the
	// client gives up, discarding a bundle that already reached quorum.
	sendCtx, cancel := context.WithTimeout(ctx, h.nodeSendTimeout)
	defer cancel()

	for _, node := range h.donConfig.Members {
		group.Go(func() error {
			err := h.don.SendToNode(sendCtx, node.Address, &ar.req)
			if err != nil {
				nodeErrors.Add(1)
				l.Errorw("error sending request to node", "node", node.Address, "error", err)
			}
			return nil
		})
	}

	_ = group.Wait()

	numNodeErrors := nodeErrors.Load()
	remainingPossibleResponses := len(h.donConfig.Members) - int(numNodeErrors)
	if remainingPossibleResponses < h.donConfig.F+1 && numNodeErrors > 0 {
		return h.sendResponseAndClearRequest(ctx, ar, h.constructErrorResponse(ar.req, api.FatalError, errors.New("failed to forward user request to nodes")))
	}

	l.Debugw("successfully forwarded request to relay nodes")
	return nil
}
```
