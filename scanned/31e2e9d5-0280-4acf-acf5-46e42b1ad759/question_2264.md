# Q2264: no response signature verification in rpc.ts

## Question
The wallet-api response is consumed after only a method-name comparison; can an attacker return a response through rpc(): builds {version:1 whose signature field is arbitrary and have it used or broadcast?

## Target
- File/function: [src/wallet-api/rpc.ts](src/wallet-api/rpc.ts) - rpc(): builds {version:1, url, method, headers:{privy-app-id, privy-request-expiry}, body}, signs it, then spreads caller headers after the signature header
- Entrypoint: provider.request(...) on a privy-v2 wallet -> walletRpc -> rpc()
- Attacker controls: the request body (chain_type, method, wallet_id, params) and the extraHeaders object
- Exploit idea: Return an arbitrary signature and observe it flowing to the caller.
- Invariant to test: Responses carrying signatures must be verified against the request and the wallet key.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: return a bogus signature from rpc(): builds {version:1's route and assert verification fails.
