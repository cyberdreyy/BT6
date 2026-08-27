### Title
Vault gateway handler leaks raw internal authorizer error text to unauthenticated callers - ([File: core/services/gateway/handlers/vault/handler.go])

### Summary
`handler.HandleJSONRPCUserMessage` returns `errors.New("request not authorized: " + err.Error())` for any authorization failure from `requestProcessor.ProcessRequest`, embedding the raw underlying error verbatim instead of a generic, redacted message. This error string is propagated unmodified to the unauthenticated caller via `gateway.ProcessRequest`.

### Finding Description
In `core/services/gateway/handlers/vault/handler.go`, after calling `h.requestProcessor.ProcessRequest(ctx, &req, cachedPublicKey)` at line 436, the handler branches on the error type: [1](#0-0) 

For `vaultcap.IsInvalidVaultParamsError(err)` it goes through `sendImmediateUserResponse`/`errorResponse` with `api.InvalidParamsError`, which prefixes the message with `"invalid params error: "` plus `err.Error()`: [2](#0-1) 

For any other error (i.e., authorization failure path, `AuthorizeRequest` error from `GatewayVaultRequestProcessor.authorizeAndStamp`), the handler does **not** go through the sanitized `errorResponse`/`callback.SendResponse` mechanism at all — it directly returns a Go `error` value containing the raw `err.Error()` text from the authorizer: [3](#0-2) 

The underlying error is constructed in `GatewayVaultRequestProcessor.authorizeAndStamp` as `fmt.Errorf("request not authorized: %w", err)`, wrapping whatever the configured `Authorizer` (`AllowListBasedAuth` or `JWTBasedAuth`) returns: [4](#0-3) 

This returned `error` propagates to `gateway.ProcessRequest`, which converts it directly into the HTTP response body without any redaction: [5](#0-4) 

So the full text of the wrapped authorizer error — whatever detail the `AllowListBasedAuth`/`JWTBasedAuth` implementations choose to include (e.g. JWT validation failure reasons, allowlist lookup outcomes) — is returned verbatim to any unauthenticated caller of the gateway HTTP endpoint. By contrast, the `InvalidParamsError` path goes through the handler's structured `errorResponse`, which at least applies a uniform prefix. Both paths embed `err.Error()` directly with no generic/redacted fallback, meaning an attacker who submits crafted, malformed or wrong-credential vault requests receives differentiated, detail-bearing error text depending on which internal check failed.

### Impact Explanation
This is an information-disclosure issue (Chainlink bounty impact class: "disclosure of confidential information / internals with no direct fund/data impact"), not secret leakage or authentication bypass. An unauthenticated or unprivileged caller can potentially use differences in error verbosity/content between the invalid-params branch and the authorization-failure branch (and among different authorizer failure causes reflected in `err.Error()`) to fingerprint which validation stage rejected the request, without gaining privileged access, secrets, or funds.

### Likelihood Explanation
No authentication or preconditions are required beyond being able to submit a JSON-RPC request to the gateway's vault endpoint — this matches the "unauthenticated client of the gateway" attacker model in scope. The flaw is trivially and repeatably reproducible: any malformed-params vs. bad-auth request pair will differ in returned error text, since both paths return `err.Error()` unmodified.

### Recommendation
Replace the direct `errors.New("request not authorized: " + err.Error())` return with a generic, fixed authorization-failure message (e.g., `errors.New("request not authorized")`) that does not embed `err.Error()`, and log the detailed error server-side only (as is already done via `h.lggr.Errorw`). Ensure `AllowListBasedAuth`/`JWTBasedAuth` error messages returned up the stack are similarly generic, with detailed diagnostics confined to logs.

### Proof of Concept
Go table test in `core/services/gateway/handlers/vault/handler_test.go`:
1. Construct three requests to `HandleJSONRPCUserMessage`:
   a. unknown owner / no matching allowlist entry (empty/invalid `Auth`)
   b. known owner with an invalid/wrong signature
   c. malformed JWT token in `Auth` header (when `Auth0` JWT-based auth configured)
2. Capture the returned `error` value (or, for cases routed through `callback.Wait`, the `RawResponse` error message) for each case.
3. Assert that the three error strings are textually generic and identical in structure (e.g., all equal to `"request not authorized"`), with no embedded authorizer-specific detail (no substrings like "jwt", "allowlist", "signature", or owner address) that would let a test distinguish which stage/reason caused the rejection.
4. Currently this assertion fails because `err.Error()` from the underlying authorizer is embedded verbatim in the response, demonstrating the disclosure.

### Citations

**File:** core/services/gateway/handlers/vault/handler.go (L436-443)
```go
	authorized, err := h.requestProcessor.ProcessRequest(ctx, &req, cachedPublicKey)
	if err != nil {
		if vaultcap.IsInvalidVaultParamsError(err) {
			return h.sendImmediateUserResponse(ctx, req, callback, api.InvalidParamsError, err)
		}
		h.lggr.Errorw("request not authorized", "method", req.Method, "requestID", req.ID, "hasAuth", req.Auth != "", "error", err)
		return errors.New("request not authorized: " + err.Error())
	}
```

**File:** core/services/gateway/handlers/vault/handler.go (L756-762)
```go
	case api.InvalidParamsError:
		paramsStr := ""
		if req.Params != nil {
			paramsStr = string(*req.Params)
		}
		h.lggr.Errorw("invalid params", "requestID", req.ID, "params", paramsStr)
		err = errors.New("invalid params error: " + err.Error())
```

**File:** core/capabilities/vault/gateway_vault_request_processor.go (L233-238)
```go
	authResult, err := p.authorizer.AuthorizeRequest(ctx, *req)
	if err != nil {
		authErr := fmt.Errorf("request not authorized: %w", err)
		p.lggr.Errorw("gateway vault request authorization failed", "method", req.Method, "requestID", req.ID, "hasAuth", req.Auth != "", "incomingOwner", incomingOwner, "error", authErr)
		return nil, authErr
	}
```

**File:** core/services/gateway/gateway.go (L270-276)
```go
	} else {
		method = jsonRequest.Method
		err = h.HandleJSONRPCUserMessage(ctx, jsonRequest, callback)
	}
	if err != nil {
		return newError(jsonRequest.ID, api.HandlerError, err.Error())
	}
```
