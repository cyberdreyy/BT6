# Q2594: chain_type chosen by the caller in rpc.ts

## Question
The signed body includes a caller-supplied chain_type; can an attacker mismatch chain_type against the wallet through rpc(): builds {version:1 so a signature valid on one chain is produced for a wallet on another?

## Target
- File/function: [src/wallet-api/rpc.ts](src/wallet-api/rpc.ts) - rpc(): builds {version:1, url, method, headers:{privy-app-id, privy-request-expiry}, body}, signs it, then spreads caller headers after the signature header
- Entrypoint: provider.request(...) on a privy-v2 wallet -> walletRpc -> rpc()
- Attacker controls: the request body (chain_type, method, wallet_id, params) and the extraHeaders object
- Exploit idea: Submit an ethereum method for a solana wallet id.
- Invariant to test: Chain type must be derived from the wallet record, not the request.
- Expected Immunefi impact: High - signed-payload integrity break: the bytes signed differ from the bytes the user approved (chain, domain, recipient, amount, or encoding confusion).
- Fast validation: Unit test: mismatch chain_type and wallet in rpc(): builds {version:1 and assert rejection.
