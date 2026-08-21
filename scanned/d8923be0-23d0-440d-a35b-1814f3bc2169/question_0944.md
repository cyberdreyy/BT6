# Q0944: expiry check is a tautology in rpc.ts

## Question
The guard compares Date.now() against a value just computed from Date.now(); can an attacker rely on this dead check so rpc(): builds {version:1 never actually rejects a stale envelope?

## Target
- File/function: [src/wallet-api/rpc.ts](src/wallet-api/rpc.ts) - rpc(): builds {version:1, url, method, headers:{privy-app-id, privy-request-expiry}, body}, signs it, then spreads caller headers after the signature header
- Entrypoint: provider.request(...) on a privy-v2 wallet -> walletRpc -> rpc()
- Attacker controls: the request body (chain_type, method, wallet_id, params) and the extraHeaders object
- Exploit idea: Trace the branch and confirm it can only trigger under an implausible delay.
- Invariant to test: Freshness must be validated against the moment of transmission.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: delay between construction and send in rpc(): builds {version:1 and assert the stale envelope is rejected.
