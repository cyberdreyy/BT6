### Title
Missing allowlist/authorization check in `HandleLegacyUserMessage` allows any signed caller to trigger `web_api_trigger` broadcasts to a DON they are not authorized to use - ([File: core/services/gateway/handlers/capabilities/handler.go])

### Summary
`handler.HandleLegacyUserMessage` validates message structure, timestamp freshness, and cryptographic signature via `msg.Validate()`/`common.ValidatedRequestFromMessage`, but performs no authorization check tying the message sender to the specific DON/workflow it targets before broadcasting the request to every member of `h.donConfig.Members` via `don.SendToNode`. The `TODO: apply allowlist and rate-limiting here` comment at line 384 confirms this control was never implemented.

### Finding Description
The flow is: `gateway.ProcessRequest` (`core/services/gateway/gateway.go`) decodes the incoming JSON-RPC request and, for legacy requests, dispatches to `handler.HandleLegacyUserMessage` (via `multiHandler.HandleLegacyUserMessage`) based solely on `msg.Body.DonId` [1](#0-0) . Inside `HandleLegacyUserMessage`, the code:
1. Unmarshals `TriggerRequestPayload`.
2. Checks `payload.Timestamp` is non-zero and not stale.
3. Immediately after the `TODO: apply allowlist and rate-limiting here` comment, checks only that `msg.Body.Method == MethodWebAPITrigger`.
4. Converts the message to a JSON-RPC request via `common.ValidatedRequestFromMessage`.
5. Saves a callback and loops over **all** `h.donConfig.Members`, forwarding the request to every node with `don.SendToNode` [2](#0-1) .

No check exists anywhere in this path that verifies the requester (identified by `msg.Body.Sender`/signature) is the owner of, or is otherwise authorized to invoke, the workflow/DON referenced in the trigger payload. The only searchable "allowlist" references in the gateway package are the TODO comment itself and unrelated test/network files, confirming no allowlist enforcement exists in this handler or its call path. `msg.Validate()` (invoked in `ValidatedMessageFromReq`/gateway routing) authenticates that the signature matches the claimed sender address, but authentication of *who* sent the message is not the same as *authorization* for *which* workflow/DON that sender may target — the exactness invariant (sender must be authorized for the specific DON/workflow referenced) is never checked.

### Impact Explanation
Any client capable of producing a validly signed gateway message (which requires only an arbitrary private key, not membership in any particular DON or workflow owner list) can have their `web_api_trigger` request broadcast to all nodes of a DON, including DONs/workflows they do not own or have any subscription/authorization for. This matches the Chainlink bounty class of "unauthorized job run" / authorization bypass — an attacker can cause capability nodes to execute/relay a trigger message intended to be gated by workflow ownership, potentially causing unwanted webhook/HTTP side effects, resource consumption from processing forged triggers, or downstream execution of workflow logic using attacker-supplied trigger data.

### Likelihood Explanation
Preconditions are minimal: the attacker needs only the ability to sign gateway messages (any valid signer/address recognized by the signature-validation step), the target DON's ID (from public gateway config), and a JSON payload for `TriggerRequestPayload`. No privileged role, node access, or prior authorization is required. This is fully reproducible and repeatable since the missing check is an unconditional code-path gap, not a race condition or timing-dependent bug.

### Recommendation
Implement the allowlist/authorization check called out in the TODO before forwarding to `don.SendToNode`: verify that `msg.Body.Sender` (or the workflow owner encoded in `payload`) is authorized for the specific `DonId`/workflow referenced by the request (e.g., against a per-DON or per-workflow allowlist/subscription registry), and reject unauthorized requests with an appropriate error code (e.g., `api.UnauthorizedError`) prior to line 417's broadcast loop. Combine this with rate-limiting keyed on sender identity.

### Proof of Concept
Go handler-level integration test plan (in `core/services/gateway/handlers/capabilities/handler_test.go` style):
1. Construct a `handler` with a `donConfig` containing DON `donA` with members `[nodeA]`, using a mock `handlers.DON`.
2. Build a valid `api.Message` with `Body.Method = MethodWebAPITrigger`, `Body.DonId = "donA"`, `Body.Sender = <attackerAddr>`, and a `TriggerRequestPayload` referencing a workflow owned by a different address (`ownerB`), signed correctly by `attackerAddr` so `msg.Validate()` passes.
3. Call `h.HandleLegacyUserMessage(ctx, msg, callback)`.
4. Assert the mock `don.SendToNode` **is** called for `nodeA` (demonstrating the request is forwarded despite the sender having no relationship to `ownerB`'s workflow), and that no authorization error response is returned to `callback`.
5. Expected (secure) behavior after fix: `SendToNode` should not be invoked, and `callback.SendResponse` should be called with an authorization/allowlist error code instead.

### Citations

**File:** core/services/gateway/gateway.go (L250-262)
```go
	} else {
		// Legacy request with DON ID - validate and fetch handler
		isLegacyRequest = true
		if err = msg.Validate(); err != nil {
			return newError(jsonRequest.ID, api.UserMessageParseError, err.Error())
		}
		handlerKey = msg.Body.DonId
		var ok bool
		h, ok = g.handlers[handlerKey]
		if !ok {
			return newError(jsonRequest.ID, api.UnsupportedDONIdError, "Unsupported DON ID: "+handlerKey)
		}
	}
```

**File:** core/services/gateway/handlers/capabilities/handler.go (L384-420)
```go
	// TODO: apply allowlist and rate-limiting here
	if msg.Body.Method != MethodWebAPITrigger {
		h.lggr.Errorw("unsupported method", "method", body.Method)
		return callback.SendResponse(handlers.UserCallbackPayload{
			RawResponse: codec.EncodeNewErrorResponse(
				msg.Body.MessageId,
				api.ToJSONRPCErrorCode(api.UnsupportedMethodError),
				"invalid method "+msg.Body.Method,
				nil,
			),
			ErrorCode: api.UnsupportedMethodError,
		})
	}
	req, err := common.ValidatedRequestFromMessage(msg)
	if err != nil {
		h.lggr.Errorw(ErrTransformingMessageToRequest)
		return callback.SendResponse(handlers.UserCallbackPayload{
			RawResponse: codec.EncodeNewErrorResponse(
				msg.Body.MessageId,
				api.ToJSONRPCErrorCode(api.UserMessageParseError),
				ErrTransformingMessageToRequest,
				nil,
			),
			ErrorCode: api.UserMessageParseError,
		})
	}

	h.mu.Lock()
	h.savedCallbacks[msg.Body.MessageId] = &savedCallback{id: msg.Body.MessageId, createdAt: time.Now(), Callback: callback}
	don := h.don
	h.mu.Unlock()

	// Send original request to all nodes
	for _, member := range h.donConfig.Members {
		err = errors.Join(err, don.SendToNode(ctx, member.Address, req))
	}
	return err
```
