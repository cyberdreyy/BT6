# Q0064: unsigned headers appended after signing in rpc.ts

## Question
rpc() signs an envelope containing only privy-app-id and privy-request-expiry, then spreads the caller's extraHeaders after the signature header; can an unprivileged attacker pass headers through provider.request(...) on a privy-v2 wallet -> walletRpc -> rpc() that are transmitted but not covered by the authorization signature, or that overwrite the signature header itself?

## Target
- File/function: [src/wallet-api/rpc.ts](src/wallet-api/rpc.ts) - rpc(): builds {version:1, url, method, headers:{privy-app-id, privy-request-expiry}, body}, signs it, then spreads caller headers after the signature header
- Entrypoint: provider.request(...) on a privy-v2 wallet -> walletRpc -> rpc()
- Attacker controls: the request body (chain_type, method, wallet_id, params) and the extraHeaders object
- Exploit idea: Call the wallet RPC path with an extraHeaders object containing privy-authorization-signature and privy-request-expiry and inspect the outgoing request.
- Invariant to test: Every header that influences server-side authorization must be inside the signed envelope and immutable afterwards.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: call rpc(): builds {version:1 with crafted extraHeaders and assert the final headers equal the signed set.
