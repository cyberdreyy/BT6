### Title
Unauthenticated JSON-RPC endpoint served with permissive CORS and no `Host` header validation enables DNS-rebinding attacks against the local RPC interface - (File: `rpc/src/rpc_service.rs`)

### Summary
The Agave JSON-RPC service, as constructed in `JsonRpcService::new`, configures its HTTP server with `cors(DomainsValidation::AllowOnly(vec![AccessControlAllowOrigin::Any]))` and never calls `allowed_hosts`/`Host`-header validation on the `ServerBuilder` for the main JSON-RPC endpoint. The only place `should_validate_hosts: true` is explicitly set is for the auxiliary REST endpoints (`process_rest`, `process_file_get`), not for the primary JSON-RPC request path, which is passed straight through via `request.into()` in `RpcRequestMiddleware::on_request`.

### Finding Description
`JsonRpcService::new` builds the RPC HTTP server as: [1](#0-0) 

This sets `AccessControlAllowOrigin::Any` for CORS on the JSON-RPC endpoint, meaning any web origin's JavaScript is permitted to read the response of a cross-origin request made to the RPC port. Meanwhile, the request-dispatch logic in `RpcRequestMiddleware::on_request` only marks `should_validate_hosts: true` for the `/v0/circulating-supply`, `/v0/total-supply`, genesis, and snapshot file paths: [2](#0-1) [3](#0-2) 

Ordinary JSON-RPC method calls (`sendTransaction`, `getAccountInfo`, `getBalance`, etc., served by `rpc_minimal`/`rpc_full`/`rpc_accounts`/`rpc_bank`) fall into the final `else { request.into() }` branch, which is not routed through a `should_validate_hosts: true` `Respond` action. Since there is no global `allowed_hosts(...)` configuration on the `ServerBuilder`, the RPC endpoint accepts requests regardless of the `Host` header presented by the client.

The validator's RPC port defaults to binding on all interfaces unless `--private-rpc` or an explicit `--rpc-bind-address` is passed: [4](#0-3) 

Combined, an attacker-controlled webpage can perform a DNS rebinding attack: it first resolves to an attacker-controlled server, then (once the DNS TTL expires) is rebound to `127.0.0.1`. The browser continues to treat requests as same-origin as the original page, allowing JavaScript to issue `fetch`/`XHR` POST requests to `http://127.0.0.1:<rpc_port>` with a JSON-RPC payload; because CORS allows `Access-Control-Allow-Origin: *`, the attacker's script can read the JSON-RPC response as well.

### Impact Explanation
This grants a remote, unauthenticated attacker (via a malicious webpage the victim happens to visit) full read/write access to any RPC method exposed by the validator/RPC node operator, including calling `sendTransaction` using local wallet/keypair-backed workflows that rely on this RPC (e.g., local dApp or CLI tooling relaying signed transactions), account/state introspection (`getAccountInfo`, `getBalance`, etc.), and any privileged/administrative JSON-RPC methods enabled on the node. Combined with permissive CORS, the attacker also gets the response data back into their page, enabling data exfiltration in addition to control. This impacts RPC request handling, one of the explicitly in-scope analog areas.

### Likelihood Explanation
Likelihood is moderate: it requires the victim to load a malicious webpage while an RPC endpoint (validator RPC, or an RPC-serving node) is running and reachable at `127.0.0.1` or a routable address, and requires the DNS rebinding infrastructure to succeed (well-documented, low-difficulty technique, matching the original report's "Low difficulty" rating). Default configurations that expose RPC on `0.0.0.0` or without `--private-rpc` increase exposure; even `127.0.0.1`-bound RPC servers remain vulnerable to this exact class of attack since DNS rebinding specifically targets localhost-bound services.

### Recommendation
Add `Host` header allow-listing on the `ServerBuilder` for the RPC HTTP server (e.g., via `jsonrpc_http_server`'s `allowed_hosts` API) so that only expected hostnames (`localhost`, the configured bind IP, etc.) are accepted, and reject requests presenting other `Host` values by default, mirroring the mitigation already applied narrowly to `process_rest`/`process_file_get`. Additionally, reconsider the blanket `AccessControlAllowOrigin::Any` CORS policy for the RPC listener, or make it opt-in/configurable rather than the default.

### Proof of Concept
1. Start a validator/RPC node with default settings so the RPC service binds and does not enforce `Host` header checks (`rpc/src/rpc_service.rs`, `ServerBuilder` construction lacking `allowed_hosts`).
2. Host a malicious webpage on `attacker.example.com`, initially resolving to an attacker server.
3. After DNS TTL expiry, rebind `attacker.example.com` to `127.0.0.1`.
4. From the now same-origin-appearing page, issue `fetch('http://attacker.example.com:<rpc_port>', {method:'POST', headers:{'Content-Type':'text/plain'}, body: JSON.stringify({jsonrpc:'2.0', id:1, method:'sendTransaction', params:[...]})})`.
5. Because the server does not validate the `Host` header and CORS allows `Origin: *`, the request reaches the JSON-RPC handler and the response is readable by the attacker's script, demonstrating full unauthenticated interaction with the RPC interface via the victim's browser.

### Citations

**File:** rpc/src/rpc_service.rs (L394-407)
```rust
        if let Some(path) = match_supply_path(request.uri().path()) {
            process_rest(self.bank_forks.clone(), path)
        } else if self.is_file_get_path(request.uri().path()) {
            self.process_file_get(request.uri().path())
        } else if request.uri().path() == "/health" {
            hyper::Response::builder()
                .status(hyper::StatusCode::OK)
                .body(hyper::Body::from(self.health_check()))
                .unwrap()
                .into()
        } else {
            request.into()
        }
    }
```

**File:** rpc/src/rpc_service.rs (L453-469)
```rust
fn process_rest(bank_forks: Arc<RwLock<BankForks>>, path: &str) -> RequestMiddlewareAction {
    let path = path.to_string();

    RequestMiddlewareAction::Respond {
        should_validate_hosts: true,
        response: Box::pin(async move {
            let result = handle_rest(&bank_forks, path.as_str()).await;
            match result {
                Some(s) => Ok(hyper::Response::builder()
                    .status(hyper::StatusCode::OK)
                    .body(hyper::Body::from(s))
                    .unwrap()),
                None => Ok(RpcRequestMiddleware::not_found()),
            }
        }),
    }
}
```

**File:** rpc/src/rpc_service.rs (L724-743)
```rust
                let server = ServerBuilder::with_meta_extractor(
                    io,
                    move |req: &hyper::Request<hyper::Body>| {
                        let xbigtable = req.headers().get("x-bigtable");
                        if xbigtable.is_some_and(|v| v == "disabled") {
                            request_processor.clone_without_bigtable()
                        } else {
                            request_processor.clone()
                        }
                    },
                )
                .event_loop_executor(runtime.handle().clone())
                .threads(1)
                .cors(DomainsValidation::AllowOnly(vec![
                    AccessControlAllowOrigin::Any,
                ]))
                .cors_max_age(86400)
                .request_middleware(request_middleware)
                .max_request_body_size(max_request_body_size)
                .start_http(&rpc_addr);
```

**File:** validator/src/commands/run/execute.rs (L504-511)
```rust
    let rpc_bind_address = if matches.is_present("rpc_bind_address") {
        solana_net_utils::parse_host(matches.value_of("rpc_bind_address").unwrap())
            .expect("invalid rpc_bind_address")
    } else if private_rpc {
        solana_net_utils::parse_host("127.0.0.1").unwrap()
    } else {
        bind_addresses.active()
    };
```
