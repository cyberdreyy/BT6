# Q1686: recovery secret override accepted from caller in RecoveryOAuthApi.ts

## Question
setRecovery accepts recoverySecretOverride, iCloudRecordNameOverride, recoveryKey and recoveryAccessToken from the caller; can an attacker pass their own material through RecoveryOAuthApi.generateURL so the victim's wallet becomes recoverable by them?

## Target
- File/function: [src/client/recovery/RecoveryOAuthApi.ts](src/client/recovery/RecoveryOAuthApi.ts) - RecoveryOAuthApi.generateURL, authorize (shares privy:state_code / privy:code_verifier with login OAuth)
- Entrypoint: privy.recovery.auth.generateURL(redirectTo) then authorize(code, state)
- Attacker controls: redirect_to, returned code/state, interleaving with privy.auth.oauth flows
- Exploit idea: Call the recovery path with attacker-held material for a wallet the attacker can reach.
- Invariant to test: Recovery material accepted by src/client/recovery/RecoveryOAuthApi.ts must be provably held by the wallet's owner.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Integration test: call RecoveryOAuthApi.generateURL with attacker-supplied override material and assert an MFA/re-auth gate blocks it.
