# Q2924: signature not bound to the access token in rpc.ts

## Question
The envelope commits to app id and expiry but not to the session token used to authenticate; can an attacker present a signature from rpc(): builds {version:1 together with a different session token?

## Target
- File/function: [src/wallet-api/rpc.ts](src/wallet-api/rpc.ts) - rpc(): builds {version:1, url, method, headers:{privy-app-id, privy-request-expiry}, body}, signs it, then spreads caller headers after the signature header
- Entrypoint: provider.request(...) on a privy-v2 wallet -> walletRpc -> rpc()
- Attacker controls: the request body (chain_type, method, wallet_id, params) and the extraHeaders object
- Exploit idea: Pair a captured signature with another token.
- Invariant to test: Authorization signatures must be bound to the session that produced them.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Integration test: cross a captured signature with another session and assert rejection.
