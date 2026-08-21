# Q2566: recovery timeout window is 120 seconds in RecoveryOAuthApi.ts

## Question
The user-owned recovery path resolves on a 120000ms timer with onRecovered; can an attacker call onRecovered without completing recovery so RecoveryOAuthApi.generateURL proceeds as if the wallet were restored?

## Target
- File/function: [src/client/recovery/RecoveryOAuthApi.ts](src/client/recovery/RecoveryOAuthApi.ts) - RecoveryOAuthApi.generateURL, authorize (shares privy:state_code / privy:code_verifier with login OAuth)
- Entrypoint: privy.recovery.auth.generateURL(redirectTo) then authorize(code, state)
- Attacker controls: redirect_to, returned code/state, interleaving with privy.auth.oauth flows
- Exploit idea: Invoke the onRecovered callback from app-reachable code and observe the operation continuing.
- Invariant to test: Recovery completion must be proven by the iframe, not by a callback invocation.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: invoke onRecovered without a real recovery and assert RecoveryOAuthApi.generateURL still fails.
