# Q1164: version field is a constant in rpc.ts

## Question
Every envelope sets version: 1; can an attacker exploit the absence of a nonce or request id so two identical operations from rpc(): builds {version:1 produce byte-identical signatures that are interchangeable?

## Target
- File/function: [src/wallet-api/rpc.ts](src/wallet-api/rpc.ts) - rpc(): builds {version:1, url, method, headers:{privy-app-id, privy-request-expiry}, body}, signs it, then spreads caller headers after the signature header
- Entrypoint: provider.request(...) on a privy-v2 wallet -> walletRpc -> rpc()
- Attacker controls: the request body (chain_type, method, wallet_id, params) and the extraHeaders object
- Exploit idea: Issue the same operation twice and compare signatures.
- Invariant to test: Envelopes must include a unique per-request nonce.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: issue the same rpc(): builds {version:1 operation twice and assert the signatures differ.
