## #Vulnerability found for this question.

### Title
Unauthenticated DoS via `strings.Repeat` negative-count panic in `normalizeHex` due to incomplete length validation - ([File: core/services/gateway/handlers/capabilities/v2/http_trigger_handler.go])

### Summary
`validateHexInput` bounds only the *raw* input string length against `expectedLength` (which already includes the `"0x"` prefix budget), but `normalizeHex` recomputes the hex-body length after unconditionally trying to strip a `"0x"` prefix that may not be present. An attacker can submit a `WorkflowOwner` (or `WorkflowID`) string of exactly `expectedLength` characters with no `"0x"` prefix (or otherwise not consuming that 2-character budget) that passes `validateHexInput` but causes `normalizeHex` to call `strings.Repeat("0", negativeCount)`, which panics.

### Finding Description
The trigger request flow is: `HandleUserTriggerRequest` → `validatedTriggerRequest` → `validateTriggerParams` → `validateWorkflowFields` → `validateWorkflowOwner`/`validateWorkflowID`, all gated by `validateHexInput`, then later `resolveWorkflowID` calls `normalizeHex`. [1](#0-0) 

`validateHexInput` only checks `len(input) > expectedLength` on the *raw* (un-trimmed) string, and only strips a `"0x"` prefix if it is actually present before calling `hex.DecodeString`: [1](#0-0) 

If a caller supplies `workflowOwner` as exactly 42 lowercase hex characters **without** an `"0x"` prefix (e.g. `"00000000000000000000000000000000000000ab"`, 42 chars), `validateHexInput` passes: `len(input)==42<=42`, it is valid hex (42 hex chars decode to 21 bytes), and it's lowercase.

Later, `resolveWorkflowID` calls `normalizeHex(workflowOwner, workflowOwnerLength /*42*/)`: [2](#0-1) [3](#0-2) 

Inside `normalizeHex`: `hexStr := strings.TrimPrefix(input, "0x")` is a no-op since the input has no `"0x"` prefix, so `hexStr` remains 42 characters. `expectedHexLength := length - 2` = 40. `strings.Repeat("0", expectedHexLength-len(hexStr))` = `strings.Repeat("0", 40-42)` = `strings.Repeat("0", -2)`, which panics with `"strings: negative Repeat count"`. The same defect applies to `WorkflowID` (length 66) via `validateWorkflowID`.

This is reachable pre-authentication: `HandleJSONRPCUserMessage` in `http_handler.go` invokes `h.triggerHandler.HandleUserTriggerRequest` directly for any inbound gateway JSON-RPC message, before any JWT/`authorizeRequest` check occurs (that check only happens after `resolveWorkflowID` succeeds): [4](#0-3) [5](#0-4) 

No `recover()` wrapper exists anywhere in `core/services/gateway/handlers/capabilities/v2` (only present in a test file), so the panic propagates until caught by Go's `net/http` per-connection recovery, aborting the specific in-flight request/connection.

Regarding the question's ownership-bypass framing: the panic occurs *before* `resolveWorkflowID` returns any `workflowID`, so it does not enable resolving or leaking a victim's `workflowID` — it only crashes the handling of that single request. `authorizeRequest`'s JWT-signer check remains the sole gate for legitimate (non-crashing) inputs, and that check is unaffected by this bug.

### Impact Explanation
This is a Denial-of-Service against a single in-flight gateway request: an unauthenticated caller can reliably crash the goroutine/connection handling their own JSON-RPC HTTP request by supplying a crafted `WorkflowOwner`/`WorkflowID` string of exactly the expected byte-length but lacking the `"0x"` prefix. This matches Chainlink's "Denial of Service" bounty impact class (node/service function disruption), scoped to per-request crash/connection-reset rather than full-node crash, since Go's HTTP server recovers panics per connection.

### Likelihood Explanation
Trivially reachable and repeatable: no authentication, no signer keys, and no prior state are required — a single crafted JSON-RPC POST to the gateway's HTTP-trigger endpoint with `workflow.workflowOwner` set to 42 raw hex characters (no `0x` prefix) or `workflow.workflowID` set to 66 raw hex characters (no `0x` prefix) triggers the panic every time.

### Recommendation
Fix `normalizeHex` (and/or `validateHexInput`) to compute lengths consistently: always operate on the hex-body length after stripping any optional `"0x"` prefix, and validate that the resulting hex-body length does not exceed `expectedLength-2` in both functions. Additionally, guard `strings.Repeat` against a negative count defensively (e.g., return an error/reject instead of padding when `expectedHexLength < len(hexStr)`).

### Proof of Concept
Go unit test targeting `normalizeHex`/`resolveWorkflowID` and a handler-level test through `HandleUserTriggerRequest`:
```go
func TestNormalizeHex_NegativeRepeatPanics(t *testing.T) {
    // 42 raw hex chars, no "0x" prefix - passes validateHexInput(input, 42)
    owner := "00000000000000000000000000000000000000ab" // len 42
    require.NoError(t, validateHexInput(owner, workflowOwnerLength))
    require.Panics(t, func() {
        normalizeHex(owner, workflowOwnerLength)
    })
}

func TestHandleUserTriggerRequest_MalformedOwnerNoPanic(t *testing.T) {
    handler, _ := createTestTriggerHandler(t)
    callback := hc.NewCallback()
    triggerReq := gateway_common.HTTPTriggerRequest{
        Workflow: gateway_common.WorkflowSelector{
            WorkflowOwner: "00000000000000000000000000000000000000ab", // 42 chars, no 0x
            WorkflowName:  "test-workflow",
            WorkflowTag:   "v1.0",
        },
        Input: []byte(`{}`),
    }
    reqBytes, _ := json.Marshal(triggerReq)
    rawParams := json.RawMessage(reqBytes)
    req := &jsonrpc.Request[json.RawMessage]{
        Version: "2.0", ID: "poc-1", Method: gateway_common.MethodWorkflowExecute, Params: &rawParams,
    }
    require.NotPanics(t, func() {
        _ = handler.HandleUserTriggerRequest(t.Context(), req, callback, time.Now())
    })
}
```
Expected (pre-fix): the first test panics inside `normalizeHex`; the second, when driven through the real HTTP transport, aborts the in-flight request/connection. Post-fix: both assertions of `require.NotPanics`/graceful `jsonrpc.ErrInvalidRequest` responses should hold.

### Citations

**File:** core/services/gateway/handlers/capabilities/v2/http_trigger_handler.go (L88-102)
```go
func (h *httpTriggerHandler) HandleUserTriggerRequest(ctx context.Context, req *jsonrpc.Request[json.RawMessage], callback handlers.Callback, requestStartTime time.Time) error {
	triggerReq, err := h.validatedTriggerRequest(ctx, req, callback)
	if err != nil {
		return err
	}

	workflowID, err := h.resolveWorkflowID(ctx, triggerReq, req.ID, callback)
	if err != nil {
		return err
	}

	key, err := h.authorizeRequest(ctx, workflowID, req, callback)
	if err != nil {
		return err
	}
```

**File:** core/services/gateway/handlers/capabilities/v2/http_trigger_handler.go (L260-276)
```go
func validateHexInput(input string, expectedLength int) error {
	if input != strings.ToLower(input) {
		return errors.New("must be lowercase")
	}

	if len(input) > expectedLength {
		return fmt.Errorf("hex string too long: expected at most %d characters, got %d", expectedLength, len(input))
	}

	hexStr := strings.TrimPrefix(input, "0x")
	_, err := hex.DecodeString(hexStr)
	if err != nil {
		return errors.New("must be a valid hex string")
	}

	return nil
}
```

**File:** core/services/gateway/handlers/capabilities/v2/http_trigger_handler.go (L326-333)
```go
// normalizeHex normalizes a hex string by stripping 0x prefix, padding with leading zeros, and adding 0x prefix back
func normalizeHex(input string, length int) string {
	hexStr := strings.TrimPrefix(input, "0x")
	// length-2 because we'll add "0x" prefix
	expectedHexLength := length - 2
	paddedHex := strings.Repeat("0", expectedHexLength-len(hexStr)) + hexStr
	return "0x" + paddedHex
}
```

**File:** core/services/gateway/handlers/capabilities/v2/http_trigger_handler.go (L347-347)
```go
	workflowOwner := normalizeHex(triggerReq.Params.Workflow.WorkflowOwner, workflowOwnerLength)
```

**File:** core/services/gateway/handlers/capabilities/v2/http_handler.go (L391-401)
```go
func (h *gatewayHandler) HandleJSONRPCUserMessage(ctx context.Context, req jsonrpc.Request[json.RawMessage], callback handlers.Callback) error {
	h.metrics.IncrementTriggerRequestCount(ctx, h.lggr)
	err := h.triggerHandler.HandleUserTriggerRequest(ctx, &req, callback, time.Now())
	if err != nil {
		h.lggr.Errorw("failed to handle user trigger request", "requestID",
			req.ID, "err", err)
		// error response is sent to the response channel by the trigger handler
		// so return nil after logging
	}
	return nil
}
```
