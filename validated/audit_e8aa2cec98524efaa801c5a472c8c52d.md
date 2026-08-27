Confirmed: `NewHTTPClient` in `httpclient.go` calls `tls.X509KeyPair(config.Mtls.Certificate, config.Mtls.PrivateKey)` (X.509 certificate parsing + RSA/ECDSA key parsing, which is CPU-intensive) before the `mtlsRequestRateLimiter.Allow(ctx)` check that occurs later in the caller `(*gatewayHandler).send` [1](#0-0) [2](#0-1) .

### Title
Global mTLS rate limiter is never charged when client-certificate parsing fails, enabling resource-exhaustion via repeatedly-invalid mTLS outbound requests - (File: core/services/gateway/handlers/capabilities/v2/http_handler.go)

### Summary
Analogous to the Nibiru precompile bug ("gas not consumed when precompile method fails"), the gateway's mTLS outbound HTTP path performs expensive certificate/key parsing work *before* it charges the global `mtlsRequestRateLimiter`. If `tls.X509KeyPair` fails (e.g. malformed key/certificate), `NewHTTPClient` returns an error and `(*gatewayHandler).send` returns immediately — the rate limiter's `Allow(ctx)` call is never reached, so no token is consumed. A workflow author who supplies malformed `Mtls` credentials on every request can repeatedly trigger this expensive, failing code path without ever being throttled by the intended global safeguard.

### Finding Description
`(*gatewayHandler).send` builds a throwaway HTTP client via `h.httpClientFactory(...)` and only afterward checks `h.mtlsRequestRateLimiter.Allow(ctx)`: [2](#0-1) 

The comment at this call site explicitly documents this ordering as an intentional protection against invalid mtls credentials — but the logic actually protects against the *opposite* case (valid client whose Send() fails), not against certificate/key parsing failures, because `httpClientFactory` (`NewHTTPClientFactory` → `NewHTTPClient`) fully constructs the TLS client, including `tls.X509KeyPair` parsing, and returns an error *before* the caller ever reaches the rate-limit check: [3](#0-2) 

The `OutboundHTTPRequest.Mtls` field (`PrivateKey`/`Certificate`) is populated by node responses that echo back data originating from an HTTP Action capability request constructed inside a user's workflow (the outbound HTTP request, including `Mtls`, is workflow/user-controlled data forwarded through the DON) [4](#0-3) . `makeOutgoingRequest` unmarshals the request and asynchronously invokes the callback that eventually calls `send`, once per HTTP action message [5](#0-4) .

Because `tls.X509KeyPair` parsing is skipped-over as "free" (it happens prior to rate-limit accounting), a workflow that always sends syntactically invalid PEM/key material for `Mtls.Certificate`/`Mtls.PrivateKey` causes the gateway to repeatedly perform certificate-parsing work on every action-capability invocation while the shared `mtlsRequestRateLimiter` token bucket is never decremented for these calls. This mirrors the reported bug class exactly: computational work is done unconditionally, but the resource-accounting step is skipped whenever the operation errors out early.

### Impact Explanation
Only the per-node/global *action* rate limiters (`perNodeRateLimiters`, `globalNodeRateLimiter`) gate `HandleNodeMessage` generically [6](#0-5) ; the dedicated `mtlsRequestRateLimiter` was added specifically because mTLS handling is more expensive and needs its own tighter quota (its default burst is 0, per `TestGatewayHandler_Send_MtlsRateLimitEnabledByDefault`). Since certificate parsing bypasses this quota entirely on the error path, an attacker workflow can force the gateway to burn CPU on repeated `tls.X509KeyPair` calls at whatever rate the (higher, generic) action rate limiter allows, defeating the purpose of the stricter mTLS-specific limiter and increasing CPU/DoS pressure on the gateway process shared across all DONs/workflows served by that gateway instance.

### Likelihood Explanation
Reachable from any workflow author able to configure an HTTP Action capability request with `Mtls` set — no gateway operator privilege required. The trigger condition (malformed certificate/key bytes) is trivial to produce deterministically, and the request path is otherwise validated/authorized like any normal action request, so this is a low-effort, repeatable DoS vector, though bounded in severity by the outer per-node/global action rate limiters.

### Recommendation
Charge (or pre-check) `mtlsRequestRateLimiter` before constructing the TLS client / parsing certificates, mirroring the general fix pattern from the referenced report: perform the rate-limit check first, and only then do the expensive key/certificate parsing and connection setup. Alternatively, move the `tls.X509KeyPair` validation behind the rate limiter inside `send`, or validate certificate well-formedness cheaply (e.g., basic PEM structural check) prior to the costly parse, charging the limiter regardless of validation outcome.

### Proof of Concept
1. As a workflow author, register a workflow with an HTTP Action capability request whose `OutboundHTTPRequest.Mtls` contains syntactically invalid `Certificate`/`PrivateKey` byte slices.
2. Trigger the workflow so that a node sends the resulting `OutboundHTTPRequest` back to the gateway via `HandleNodeMessage` → `makeOutgoingRequest` → `send` [7](#0-6) .
3. `h.httpClientFactory` → `NewHTTPClient` calls `tls.X509KeyPair` which fails and returns an error immediately [1](#0-0) ; `send` returns this error to the caller without ever calling `h.mtlsRequestRateLimiter.Allow(ctx)`.
4. Repeat step 2 at the rate allowed by the (looser) per-node/global action rate limiters; observe (e.g., via metrics/instrumentation on `mtlsRequestRateLimiter`) that its token bucket is never drawn down despite repeated certificate-parsing work being performed, unlike the case of a request that reaches the limiter check.

### Citations

**File:** core/services/gateway/network/httpclient.go (L295-319)
```go
	if config.Mtls != nil {
		// Defence-in-depth protection against accidental reuse
		// of the HTTP client leading to auth'd connections leaking across
		// users.
		defaultTransport.DisableKeepAlives = true
		defaultTransport.TLSHandshakeTimeout = 10 * time.Second

		cert, err := tls.X509KeyPair(config.Mtls.Certificate, config.Mtls.PrivateKey)
		if err != nil {
			return nil, fmt.Errorf("failed to parse MtlsAuth into KeyPair: %w", err)
		}

		defaultTransport.TLSClientConfig = &tls.Config{
			Certificates: []tls.Certificate{cert},
			MinVersion:   tls.VersionTLS12,
		}
		safeConfigBuilder.SetTransport(defaultTransport)

		if config.ConcurrencyLimiter == nil {
			return nil, errors.New("mtls requires a ConcurrencyLimiter")
		}
		client = &concurrencyLimitedClient{
			client:  safeurl.Client(safeConfigBuilder.Build()),
			limiter: config.ConcurrencyLimiter,
		}
```

**File:** core/services/gateway/handlers/capabilities/v2/http_handler.go (L238-254)
```go
func (h *gatewayHandler) HandleNodeMessage(ctx context.Context, resp *jsonrpc.Response[json.RawMessage], nodeAddr string) error {
	if resp.ID == "" {
		return fmt.Errorf("received response with empty request ID from node %s", nodeAddr)
	}
	h.lggr.Debugw("handling incoming node message", "requestID", resp.ID, "nodeAddr", nodeAddr)
	nodeRateLimiter, ok := h.perNodeRateLimiters[nodeAddr]
	if !ok {
		return fmt.Errorf("received message from unexpected node %s", nodeAddr)
	}
	if !nodeRateLimiter.Allow(ctx) {
		h.metrics.IncrementCapabilityNodeThrottled(ctx, nodeAddr, h.lggr)
		return fmt.Errorf("rate limit exceeded for node %s", nodeAddr)
	}
	if !h.globalNodeRateLimiter.Allow(ctx) {
		h.metrics.IncrementGlobalThrottled(ctx, h.lggr)
		return errors.New("global rate limit exceeded")
	}
```

**File:** core/services/gateway/handlers/capabilities/v2/http_handler.go (L297-334)
```go
func (h *gatewayHandler) send(ctx context.Context, httpReq network.HTTPRequest, req gateway_common.OutboundHTTPRequest) (*network.HTTPResponse, error) {
	if req.Mtls == nil {
		return h.httpClient.Send(ctx, httpReq)
	}

	if h.httpClientFactory == nil {
		return nil, errors.New("nil http client factory, cannot make mtls request")
	}

	// Instantiate a throwaway HTTP client with the provided Mtls client certificate provided.
	// We do this to ensure that we don't accidentally leak auth'd connections to other users.
	// Note: this isn't a DOS vector because
	// a) we have a global rate limit above which limits abuse
	// b) we apply rate limits limiting the ability of sending nodes to spam requests
	// c) we apply per-owner rate limits in the action capability in the
	// workflow node limiting the ability of users to abuse this flow by spamming Mtls requests.
	// The client enforces the mtls concurrency limit internally (on the request's
	// capped-timeout context) before delegating to the underlying transport.
	client, err := h.httpClientFactory(network.HTTPClientConfig{
		Mtls: &gateway_common.MtlsAuth{
			PrivateKey:  req.Mtls.PrivateKey,
			Certificate: req.Mtls.Certificate,
		},
		ConcurrencyLimiter: h.mtlsConcurrencyLimiter,
	})
	if err != nil {
		return nil, fmt.Errorf("failed to instantiate http client for mtls request: %w", err)
	}

	// We don't have access to the org here, so this will fall back to the environment default (=false).
	// That's appropriate because all fields set on the request come from untrusted nodes.
	// The capability separately applies an org-specific check.

	// Note: we intentionally consume the rate-limit after instantiating the client so that a malicious user
	// can't send requests with invalid mtls credentials and thus cheaply consume global tokens.
	if !h.mtlsRequestRateLimiter.Allow(ctx) {
		return nil, fmt.Errorf("global mtls request rate limit exceeded: %w", network.ErrBlockedRequest)
	}
```

**File:** core/services/gateway/handlers/capabilities/v2/http_handler.go (L403-437)
```go
func (h *gatewayHandler) makeOutgoingRequest(ctx context.Context, resp *jsonrpc.Response[json.RawMessage], nodeAddr string) error {
	requestID := resp.ID
	h.lggr.Debugw("handling outgoing message", "requestID", requestID, "nodeAddr", nodeAddr)
	var req gateway_common.OutboundHTTPRequest
	err := json.Unmarshal(*resp.Result, &req)
	if err != nil {
		return fmt.Errorf("failed to unmarshal HTTP request from node %s: %w", nodeAddr, err)
	}
	timeout := time.Duration(req.TimeoutMs) * time.Millisecond
	httpReq := network.HTTPRequest{
		Method:           req.Method,
		URL:              req.URL,
		Headers:          req.Headers, //nolint:staticcheck // forward deprecated Headers for backward compatibility; request uses MultiHeaders when set
		MultiHeaders:     req.MultiHeaders,
		Body:             req.Body,
		MaxResponseBytes: req.MaxResponseBytes,
		Timeout:          timeout,
	}

	sendResponseTimeout := time.Duration(defaultSendResponseTimeoutMs) * time.Millisecond

	// send response to node async
	h.wg.Go(func() {
		// not cancelled when parent is cancelled to ensure the goroutine can finish
		baseCtx := context.WithoutCancel(ctx)
		httpCtx, httpCancel := context.WithTimeout(baseCtx, timeout)
		defer httpCancel()
		l := logger.With(h.lggr, "requestID", requestID, "method", req.Method, "timeout", req.TimeoutMs)
		var outboundResp gateway_common.OutboundHTTPResponse
		callback := h.createHTTPRequestCallback(httpCtx, requestID, httpReq, req)
		if req.CacheSettings.MaxAgeMs > 0 {
			h.metrics.IncrementCacheReadCount(ctx, h.lggr)
			outboundResp = h.responseCache.Fetch(httpCtx, req, callback, req.CacheSettings.Store)
		} else {
			outboundResp = callback()
```
