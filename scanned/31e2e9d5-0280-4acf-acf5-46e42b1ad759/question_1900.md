# Q1900: recovery key material fetched by address in withMfa.ts

## Question
RecoveryApi.getRecoveryKeyMaterial takes an address path param and chain_type body; can an attacker request material for an address that is not theirs through withMfa retry loop (4 attempts?

## Target
- File/function: [src/embedded/withMfa.ts](src/embedded/withMfa.ts) - withMfa retry loop (4 attempts, 300000ms per MFA wait, mfaAlwaysRequired flag)
- Entrypoint: every EmbeddedWalletProxy.invokeWithMfa operation
- Attacker controls: timing of mfa promise resolution, error types returned into the loop
- Exploit idea: Call the method with another user's wallet address.
- Invariant to test: Recovery material requests must be scoped to wallets owned by the authenticated user.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Integration test: call withMfa retry loop (4 attempts with a foreign address and assert the SDK refuses before the request.
