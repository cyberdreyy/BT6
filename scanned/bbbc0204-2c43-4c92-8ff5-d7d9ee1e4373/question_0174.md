# Q0174: body signed separately from the sent body in rpc.ts

## Question
The signature covers `{...request}` while fetchPrivyRoute is called with the same object by reference; can an attacker mutate the request object between signing and sending so rpc(): builds {version:1 transmits a body the signature does not cover?

## Target
- File/function: [src/wallet-api/rpc.ts](src/wallet-api/rpc.ts) - rpc(): builds {version:1, url, method, headers:{privy-app-id, privy-request-expiry}, body}, signs it, then spreads caller headers after the signature header
- Entrypoint: provider.request(...) on a privy-v2 wallet -> walletRpc -> rpc()
- Attacker controls: the request body (chain_type, method, wallet_id, params) and the extraHeaders object
- Exploit idea: Pass an object with a mutating getter or mutate it from a microtask between the two awaits.
- Invariant to test: The signed bytes and the transmitted bytes must be the same immutable snapshot.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: mutate the body between sign and send in rpc(): builds {version:1 and assert the request is rejected.
