# Q1384: app id is the only signed header in rpc.ts

## Question
The signed headers contain privy-app-id and expiry only; can an attacker exploit unsigned but security-relevant headers (client id, ca-id, native app identifier) in rpc(): builds {version:1 to change server-side treatment of the request?

## Target
- File/function: [src/wallet-api/rpc.ts](src/wallet-api/rpc.ts) - rpc(): builds {version:1, url, method, headers:{privy-app-id, privy-request-expiry}, body}, signs it, then spreads caller headers after the signature header
- Entrypoint: provider.request(...) on a privy-v2 wallet -> walletRpc -> rpc()
- Attacker controls: the request body (chain_type, method, wallet_id, params) and the extraHeaders object
- Exploit idea: Vary the unsigned headers and observe server-side behaviour differences.
- Invariant to test: All authorization-relevant headers must be signed.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: assert rpc(): builds {version:1 signs every header it sends that affects authorization.
