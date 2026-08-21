# Q1054: update-wallet envelope carries no expiry in rpc.ts

## Question
updateWallet signs {version, url, method, headers:{privy-app-id}, body} with no privy-request-expiry; can an attacker capture that signature through rpc(): builds {version:1 and replay the signer-set change indefinitely?

## Target
- File/function: [src/wallet-api/rpc.ts](src/wallet-api/rpc.ts) - rpc(): builds {version:1, url, method, headers:{privy-app-id, privy-request-expiry}, body}, signs it, then spreads caller headers after the signature header
- Entrypoint: provider.request(...) on a privy-v2 wallet -> walletRpc -> rpc()
- Attacker controls: the request body (chain_type, method, wallet_id, params) and the extraHeaders object
- Exploit idea: Capture the authorization signature from a session-signer update and replay it later.
- Invariant to test: Every authorization signature must be time-bounded and single-use.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Integration test: replay a captured update signature via rpc(): builds {version:1 and assert rejection.
