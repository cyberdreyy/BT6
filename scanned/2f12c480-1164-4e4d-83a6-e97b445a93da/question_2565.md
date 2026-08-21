# Q2565: recovery timeout window is 120 seconds in RecoveryApi.ts

## Question
The user-owned recovery path resolves on a 120000ms timer with onRecovered; can an attacker call onRecovered without completing recovery so RecoveryApi.getRecoveryKeyMaterial proceeds as if the wallet were restored?

## Target
- File/function: [src/client/recovery/RecoveryApi.ts](src/client/recovery/RecoveryApi.ts) - RecoveryApi.getRecoveryKeyMaterial, auth, icloudAuth
- Entrypoint: privy.recovery.getRecoveryKeyMaterial(address, chainType)
- Attacker controls: address path param, chain_type body value
- Exploit idea: Invoke the onRecovered callback from app-reachable code and observe the operation continuing.
- Invariant to test: Recovery completion must be proven by the iframe, not by a callback invocation.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: invoke onRecovered without a real recovery and assert RecoveryApi.getRecoveryKeyMaterial still fails.
