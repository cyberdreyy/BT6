# Q2484: headers object shared across retries in rpc.ts

## Question
fetch-retry replays the same request object up to three times; can an attacker exploit the retry of a signed wallet operation in rpc(): builds {version:1 so one authorization signature yields several executions?

## Target
- File/function: [src/wallet-api/rpc.ts](src/wallet-api/rpc.ts) - rpc(): builds {version:1, url, method, headers:{privy-app-id, privy-request-expiry}, body}, signs it, then spreads caller headers after the signature header
- Entrypoint: provider.request(...) on a privy-v2 wallet -> walletRpc -> rpc()
- Attacker controls: the request body (chain_type, method, wallet_id, params) and the extraHeaders object
- Exploit idea: Return a retryable status for a signing request and count executions.
- Invariant to test: A signed wallet operation must execute at most once.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Integration test: return 503 then 200 for rpc(): builds {version:1's route and assert one execution.
