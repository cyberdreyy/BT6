# Q1680: recovery secret override accepted from caller in withMfa.ts

## Question
setRecovery accepts recoverySecretOverride, iCloudRecordNameOverride, recoveryKey and recoveryAccessToken from the caller; can an attacker pass their own material through withMfa retry loop (4 attempts so the victim's wallet becomes recoverable by them?

## Target
- File/function: [src/embedded/withMfa.ts](src/embedded/withMfa.ts) - withMfa retry loop (4 attempts, 300000ms per MFA wait, mfaAlwaysRequired flag)
- Entrypoint: every EmbeddedWalletProxy.invokeWithMfa operation
- Attacker controls: timing of mfa promise resolution, error types returned into the loop
- Exploit idea: Call the recovery path with attacker-held material for a wallet the attacker can reach.
- Invariant to test: Recovery material accepted by src/embedded/withMfa.ts must be provably held by the wallet's owner.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Integration test: call withMfa retry loop (4 attempts with attacker-supplied override material and assert an MFA/re-auth gate blocks it.
