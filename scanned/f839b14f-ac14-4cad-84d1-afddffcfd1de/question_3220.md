# Q3220: set-recovery runs after _load succeeded in withMfa.ts

## Question
setRecovery loads the wallet then changes recovery; can an attacker interrupt between load and set so withMfa retry loop (4 attempts rebinds recovery for a different wallet than the one loaded?

## Target
- File/function: [src/embedded/withMfa.ts](src/embedded/withMfa.ts) - withMfa retry loop (4 attempts, 300000ms per MFA wait, mfaAlwaysRequired flag)
- Entrypoint: every EmbeddedWalletProxy.invokeWithMfa operation
- Attacker controls: timing of mfa promise resolution, error types returned into the loop
- Exploit idea: Swap the wallet object between the two awaits.
- Invariant to test: Load and rebind must operate on the same wallet identity.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Integration test: mutate the wallet between the awaits of withMfa retry loop (4 attempts and assert the operation aborts.
