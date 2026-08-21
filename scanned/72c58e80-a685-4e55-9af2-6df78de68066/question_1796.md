# Q1796: recovery flow shares PKCE storage with login in RecoveryOAuthApi.ts

## Question
RecoveryOAuthApi.generateURL/authorize use the same privy:state_code and privy:code_verifier keys as login OAuth; can an attacker interleave the flows so a recovery authorization consumes a login verifier or vice versa?

## Target
- File/function: [src/client/recovery/RecoveryOAuthApi.ts](src/client/recovery/RecoveryOAuthApi.ts) - RecoveryOAuthApi.generateURL, authorize (shares privy:state_code / privy:code_verifier with login OAuth)
- Entrypoint: privy.recovery.auth.generateURL(redirectTo) then authorize(code, state)
- Attacker controls: redirect_to, returned code/state, interleaving with privy.auth.oauth flows
- Exploit idea: Start a login OAuth flow, then a recovery flow, and complete them out of order.
- Invariant to test: Recovery and login authorization material must be stored under distinct, flow-scoped keys.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: start both flows against one Storage and assert the second does not overwrite the first's verifier.
