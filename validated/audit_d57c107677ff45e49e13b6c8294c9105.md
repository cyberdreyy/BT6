## Title
Unhandled panic via negative `strings.Repeat` count in `normalizeHex` reachable from unauthenticated HTTP trigger requests - (File: core/services/gateway/handlers/capabilities/v2/http_trigger_handler.go)

### Summary
The Gateway's `httpTriggerHandler.HandleUserTriggerRequest` normalizes user-supplied `workflowID`/`workflowOwner` hex strings via `normalizeHex` before any authentication or authorization check occurs. `normalizeHex` computes a padding length as `expectedHexLength - len(hexStr)` and passes it directly to `strings.Repeat`, which panics if given a negative count. The preceding length validation (`validateHexInput`) checks `len(input) > expectedLength` but does not require the `"0x"` prefix to be present, so a caller can submit a hex string at the maximum allowed length with no `"0x"` prefix and cause `strings.Repeat` to receive a negative argument, triggering a runtime panic. This mirrors the reported bug class: an `expect`/panic-style failure triggerable during the "normal" validation path of untrusted, attacker-controlled input.

### Finding Description
`validatedTriggerRequest` → `validateTriggerParams` → `validateWorkflowFields` → `validateWorkflowID`/`validateWorkflowOwner` call `validateHexInput`: [1](#0-0) 

This only enforces `len(input) <= expectedLength` and does not require a `"0x"` prefix (it only strips one if present before hex-decoding). Execution then proceeds — still before `authorizeRequest` — into `resolveWorkflowID`, which calls `normalizeHex`: [2](#0-1) 

`normalizeHex` computes `expectedHexLength := length - 2` and then `strings.Repeat("0", expectedHexLength-len(hexStr))`. If the caller supplies an `input` string with **no `"0x"` prefix** but exactly at `expectedLength` characters (which passes `validateHexInput`), `hexStr` retains the full length (nothing was trimmed), making `expectedHexLength-len(hexStr)` negative (e.g., `-2`), and `strings.Repeat` panics with `"negative Repeat count"`.

Critically, `resolveWorkflowID` (and thus `normalizeHex`) executes in `HandleUserTriggerRequest` **before** `authorizeRequest` and `checkRateLimit`: [3](#0-2) 

This request flow is invoked synchronously from the gateway's HTTP entrypoint for any client, with no authentication required to reach this code: [4](#0-3) [5](#0-4) 

No `recover()` was found anywhere in the `core/services/gateway` package outside of test files, so a panic here is not caught by any Gateway-internal safety net; it will propagate to the Go `net/http` server's per-connection panic handling.

### Impact Explanation
An unprivileged, unauthenticated actor can send a crafted `workflows.execute` HTTP trigger request (no valid JWT/auth key required to reach this code path) that panics the goroutine servicing that HTTP request. While Go's standard `net/http` server recovers panics at the per-connection level (logging "http: panic serving" and closing that connection) rather than crashing the whole process, this still represents:
- An availability/DoS primitive against the Gateway's request-handling path, reachable with zero authentication.
- Noisy, attacker-controlled panic logging that could be used to degrade service or mask other attacks.
- A concrete violation of the intended validation invariant (`validateHexInput` should guarantee `normalizeHex` never underflows), showing an authentication-adjacent input-validation gap identical in class to the reported MobileCoin `expect()`-panic issue.

### Likelihood Explanation
High. The trigger is a single, unauthenticated HTTP POST with a JSON body containing a `workflowID` (or `workflowOwner`) hex string of the maximum allowed length without a `"0x"` prefix. No valid signature, JWT, or workflow registration is required to reach `resolveWorkflowID`, since `authorizeRequest` runs strictly after this code.

### Recommendation
- **Short term:** In `validateHexInput`, require and enforce a canonical `"0x"`-prefixed form (or otherwise make `normalizeHex`'s length arithmetic robust) so that `expectedHexLength-len(hexStr)` can never be negative. Guard `normalizeHex` explicitly, e.g. return an error instead of panicking when `len(hexStr) > expectedHexLength`.
- **Long term:** Add fuzz tests around all Gateway JSON-RPC user-facing parameter parsing/normalization functions (especially anything invoked before authentication, like `resolveWorkflowID`), and add panic-recovery instrumentation/metrics at the Gateway request-handling boundary (`gateway.ProcessRequest`) to prevent any single crafted request from disrupting connection handling for other users.

### Proof of Concept
1. Determine `workflowIDLength` (the constant used in `validateWorkflowID`), e.g. assume it is 66 (`"0x"` + 64 hex chars).
2. Send an unauthenticated `workflows.execute` request to the Gateway's HTTP endpoint with:
   ```json
   {
     "jsonrpc": "2.0",
     "id": "poc-1",
     "method": "workflows.execute",
     "params": {
       "workflow": { "workflowID": "<66 lowercase hex chars with NO '0x' prefix>" },
       "input": {}
     }
   }
   ```
3. `validateHexInput` passes (`len(input) == 66 <= 66`, valid lowercase hex).
4. `resolveWorkflowID` → `normalizeHex(workflowID, 66)` computes `expectedHexLength = 64`, `hexStr` (unchanged, 66 chars since no `"0x"` prefix to trim), so `strings.Repeat("0", 64-66)` panics with `"strings: negative Repeat count"`.
5. The panic propagates out of `HandleUserTriggerRequest`/`HandleJSONRPCUserMessage`/`gateway.ProcessRequest` into the `net/http` handler goroutine servicing the HTTP request, which Go's server recovers per-connection, terminating that request/connection.

Note: I could not verify the exact numeric values of `workflowIDLength`/`workflowOwnerLength` from the excerpts retrieved (only their declaration locations were found, not the literal values), so the PoC uses a placeholder length; the underlying arithmetic bug is confirmed directly from the `normalizeHex`/`validateHexInput` source shown above regardless of the specific constant value.

### Citations

**File:** core/services/gateway/handlers/capabilities/v2/http_trigger_handler.go (L88-106)
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

	if err = h.checkRateLimit(ctx, workflowID, req.ID, callback); err != nil {
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

**File:** core/services/gateway/handlers/capabilities/v2/http_trigger_handler.go (L326-358)
```go
// normalizeHex normalizes a hex string by stripping 0x prefix, padding with leading zeros, and adding 0x prefix back
func normalizeHex(input string, length int) string {
	hexStr := strings.TrimPrefix(input, "0x")
	// length-2 because we'll add "0x" prefix
	expectedHexLength := length - 2
	paddedHex := strings.Repeat("0", expectedHexLength-len(hexStr)) + hexStr
	return "0x" + paddedHex
}

func (h *httpTriggerHandler) resolveWorkflowID(ctx context.Context, triggerReq *jsonrpc.Request[gateway_common.HTTPTriggerRequest], requestID string, callback handlers.Callback) (string, error) {
	h.lggr.Debugw("resolving workflow ID", "workflowID", triggerReq.Params.Workflow.WorkflowID, "workflowOwner", triggerReq.Params.Workflow.WorkflowOwner, "workflowName", triggerReq.Params.Workflow.WorkflowName, "workflowTag", triggerReq.Params.Workflow.WorkflowTag, "requestID", requestID)
	workflowID := triggerReq.Params.Workflow.WorkflowID
	if workflowID != "" {
		workflowID = normalizeHex(workflowID, workflowIDLength)
		_, found := h.workflowMetadataHandler.GetWorkflowReference(workflowID)
		if !found {
			h.handleUserError(ctx, requestID, jsonrpc.ErrInvalidRequest, fmt.Sprintf("Workflow not found. 'workflowID' %s is not a valid workflow ID", workflowID), callback)
			return "", errors.New("workflow not found")
		}
		return workflowID, nil
	}
	workflowOwner := normalizeHex(triggerReq.Params.Workflow.WorkflowOwner, workflowOwnerLength)
	workflowName := "0x" + hex.EncodeToString([]byte(workflows.HashTruncateName(triggerReq.Params.Workflow.WorkflowName)))
	workflowID, found := h.workflowMetadataHandler.GetWorkflowID(
		workflowOwner,
		workflowName,
		triggerReq.Params.Workflow.WorkflowTag,
	)
	if !found {
		h.handleUserError(ctx, requestID, jsonrpc.ErrInvalidRequest, "Workflow not found. Provide either a valid 'workflowID' or a valid combination of 'workflowOwner', 'workflowName', and 'workflowTag'", callback)
		return "", errors.New("workflow not found")
	}
	return workflowID, nil
```

**File:** core/services/gateway/gateway.go (L264-292)
```go
	startTime := time.Now()
	var method string
	callback := handlerscommon.NewCallback()
	if isLegacyRequest {
		method = msg.Body.Method
		err = h.HandleLegacyUserMessage(ctx, msg, callback)
	} else {
		method = jsonRequest.Method
		err = h.HandleJSONRPCUserMessage(ctx, jsonRequest, callback)
	}
	if err != nil {
		return newError(jsonRequest.ID, api.HandlerError, err.Error())
	}

	response, err := callback.Wait(ctx)
	duration := time.Since(startTime)
	if err != nil {
		response := api.RequestTimeoutError
		g.gMetrics.RecordUserMsgHandlerDuration(ctx, method, response.String(), duration)
		g.gMetrics.RecordUserMsgHandlerInvocation(ctx, method, response.String())
		return newError(jsonRequest.ID, response, "handler timeout: "+err.Error())
	}
	g.gMetrics.RecordUserMsgHandlerDuration(ctx, method, response.ErrorCode.String(), duration)
	g.gMetrics.RecordUserMsgHandlerInvocation(ctx, method, response.ErrorCode.String())

	g.lggr.Debugw("received response from handler", "handler", handlerKey, "response", response, "requestID", jsonRequest.ID)
	promRequest.WithLabelValues(response.ErrorCode.String()).Inc()
	return response.RawResponse, api.ToHttpErrorCode(response.ErrorCode)
}
```

**File:** core/services/gateway/network/httpserver.go (L180-222)
```go
func (s *httpServer) handleRequest(w http.ResponseWriter, r *http.Request) {
	if s.config.CORSEnabled {
		origin := r.Header.Get("Origin")
		if s.isAllowedOrigin(origin) {
			w.Header().Set("Access-Control-Allow-Origin", origin)
			w.Header().Set("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
			w.Header().Set("Access-Control-Allow-Headers", "Content-Type")
		}

		// handle preflight requests
		if r.Method == http.MethodOptions {
			w.WriteHeader(http.StatusNoContent)
			return
		}
	}

	maxRequestBytes, err := s.config.MaxRequestBytesLimiter.Limit(r.Context())
	if err != nil {
		msg := "Failed to get request size limit"
		s.lggr.Errorw(msg, "err", err)
		http.Error(w, msg, http.StatusInternalServerError)
		return
	}
	source := http.MaxBytesReader(nil, r.Body, int64(maxRequestBytes))
	rawMessage, err := io.ReadAll(source)
	if err != nil {
		s.lggr.Error("error reading request", err)
		w.WriteHeader(http.StatusBadRequest)
		return
	}

	// Optionally extract jwt token from authorization header
	authHeader := r.Header.Get("Authorization")
	jwtToken := ""
	if authHeader != "" {
		jwtToken = strings.TrimPrefix(authHeader, "Bearer ")
	}

	startTime := time.Now()
	rawResponse, httpStatusCode := s.handler.ProcessRequest(r.Context(), rawMessage, jwtToken)
	duration := time.Since(startTime)
	s.hMetrics.RecordRequestDuration(r.Context(), httpStatusCode, duration)
	s.hMetrics.RecordRequestCount(r.Context(), httpStatusCode)
```
