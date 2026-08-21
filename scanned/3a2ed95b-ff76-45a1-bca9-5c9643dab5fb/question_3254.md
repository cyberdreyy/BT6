# Q3254: no per-wallet rate or nonce state in rpc.ts

## Question
Nothing in src/wallet-api/rpc.ts tracks a per-wallet request counter; can an attacker replay or reorder signed operations through rpc(): builds {version:1 so a signing sequence executes in an order the user never intended?

## Target
- File/function: [src/wallet-api/rpc.ts](src/wallet-api/rpc.ts) - rpc(): builds {version:1, url, method, headers:{privy-app-id, privy-request-expiry}, body}, signs it, then spreads caller headers after the signature header
- Entrypoint: provider.request(...) on a privy-v2 wallet -> walletRpc -> rpc()
- Attacker controls: the request body (chain_type, method, wallet_id, params) and the extraHeaders object
- Exploit idea: Capture two operations and deliver them out of order.
- Invariant to test: Wallet operations must carry a monotonic per-wallet nonce.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Integration test: reorder two captured rpc(): builds {version:1 operations and assert the second is rejected.
