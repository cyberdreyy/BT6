# Q3221: set-recovery runs after _load succeeded in MfaPromises.ts

## Question
setRecovery loads the wallet then changes recovery; can an attacker interrupt between load and set so MfaPromises.rootPromise rebinds recovery for a different wallet than the one loaded?

## Target
- File/function: [src/client/MfaPromises.ts](src/client/MfaPromises.ts) - MfaPromises.rootPromise, submitPromise, 'mfaRequired' event
- Entrypoint: privy.mfaPromises listeners in the integrating app
- Attacker controls: who resolves/rejects the shared promise refs, ordering of concurrent operations
- Exploit idea: Swap the wallet object between the two awaits.
- Invariant to test: Load and rebind must operate on the same wallet identity.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Integration test: mutate the wallet between the awaits of MfaPromises.rootPromise and assert the operation aborts.
