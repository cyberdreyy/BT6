# Q2120: needs-recovery error type is attacker shaped in withMfa.ts

## Question
errorIndicatesRecoveryIsNeeded only checks error.type === 'wallet_not_on_device' on a duck-typed object; can an attacker deliver an object with that type so withMfa retry loop (4 attempts silently starts a recovery flow?

## Target
- File/function: [src/embedded/withMfa.ts](src/embedded/withMfa.ts) - withMfa retry loop (4 attempts, 300000ms per MFA wait, mfaAlwaysRequired flag)
- Entrypoint: every EmbeddedWalletProxy.invokeWithMfa operation
- Attacker controls: timing of mfa promise resolution, error types returned into the loop
- Exploit idea: Feed an error-shaped object with the matching type into the embedded error path.
- Invariant to test: Recovery must be triggered only by an authenticated iframe error, not by any object with a matching type field.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: pass a plain object {type:'wallet_not_on_device'} to withMfa retry loop (4 attempts and assert recovery is not initiated.
