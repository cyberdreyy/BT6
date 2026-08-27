### No Vulnerability found for this question.

Analysis: In `core/services/gateway/handlers/vault/handler.go`, each `activeRequest` is created and keyed strictly by its own `req.ID` in `newActiveRequest` [1](#0-0) , and node responses are only ever added to an `activeRequest` via `HandleNodeMessage`, which looks up the specific `ar` by `resp.ID` before calling `ar.addResponseForNode` [2](#0-1) . This means `ar.responses` (and thus `ar.copiedResponses()`) can never contain data belonging to a different request/activeRequest — the map is populated exclusively from node responses matching that same request ID.

In `removeExpiredRequests`, the loop iterates over `expiredRequests` and, for each `er`, calls `er.copiedResponses()` and immediately builds `nodeResponsesStr` from `er`'s own responses before synchronously calling `h.sendResponse(ctx, er, ...)` for that same `er` [3](#0-2) . There is no goroutine spawned inside the loop and no shared/reused variable across iterations that could leak the wrong `er`'s data into another's response — the loop body runs to completion (including the `sendResponse` call) before moving to the next expired request. Since `nodeResponsesStr` is derived solely from `er.responses`, which is confined to that specific request's own node responses, the error message returned to the caller in `errorResponse`/`sendResponse` is always scoped to the requester's own `activeRequest`. There is no mechanism by which an unprivileged client triggering `errorResponse` for its own request could receive another user's `activeRequest` response data.

### Citations

**File:** core/services/gateway/handlers/vault/handler.go (L381-392)
```go
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

**File:** core/services/gateway/handlers/vault/handler.go (L498-510)
```go
	ar := h.getActiveRequest(resp.ID)
	if ar == nil {
		// Request is not found, so we don't need to send a response to the user
		// This can happen if a slow node responds after the request has already been completed
		l.Debugw("no pending request found for ID")
		return nil
	}

	ok := ar.addResponseForNode(nodeAddr, resp)
	if !ok {
		l.Errorw("duplicate response from node, ignoring", "nodeAddr", nodeAddr)
		return nil
	}
```
