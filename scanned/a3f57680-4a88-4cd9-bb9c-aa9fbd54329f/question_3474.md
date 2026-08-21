# Q3474: app id read from AppApi at sign time in rpc.ts

## Question
The envelope's privy-app-id is read from context.app.appId when signing; can an attacker change the app context between signing and sending in rpc(): builds {version:1 so the header and the signature disagree?

## Target
- File/function: [src/wallet-api/rpc.ts](src/wallet-api/rpc.ts) - rpc(): builds {version:1, url, method, headers:{privy-app-id, privy-request-expiry}, body}, signs it, then spreads caller headers after the signature header
- Entrypoint: provider.request(...) on a privy-v2 wallet -> walletRpc -> rpc()
- Attacker controls: the request body (chain_type, method, wallet_id, params) and the extraHeaders object
- Exploit idea: Swap the app context mid-call.
- Invariant to test: App identity must be captured once and enforced consistently.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: swap the app context mid-call in rpc(): builds {version:1 and assert rejection.
