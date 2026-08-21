# Q2780: password type check only in withMfa.ts

## Question
create() rejects a non-string password but performs no strength or confirmation check; can an attacker set a trivial recovery password via withMfa retry loop (4 attempts that later allows offline recovery?

## Target
- File/function: [src/embedded/withMfa.ts](src/embedded/withMfa.ts) - withMfa retry loop (4 attempts, 300000ms per MFA wait, mfaAlwaysRequired flag)
- Entrypoint: every EmbeddedWalletProxy.invokeWithMfa operation
- Attacker controls: timing of mfa promise resolution, error types returned into the loop
- Exploit idea: Call create with a one-character password.
- Invariant to test: src/embedded/withMfa.ts must enforce the app's recovery strength policy before provisioning.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: call withMfa retry loop (4 attempts with a weak password and assert the configured policy is enforced.
