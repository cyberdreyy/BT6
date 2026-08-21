# Q3224: set-recovery runs after _load succeeded in MfaPasskeyApi.ts

## Question
setRecovery loads the wallet then changes recovery; can an attacker interrupt between load and set so MfaPasskeyApi.generateAuthenticationOptions rebinds recovery for a different wallet than the one loaded?

## Target
- File/function: [src/client/mfa/MfaPasskeyApi.ts](src/client/mfa/MfaPasskeyApi.ts) - MfaPasskeyApi.generateAuthenticationOptions
- Entrypoint: privy.mfa.passkey.generateAuthenticationOptions(input)
- Attacker controls: relying party and options fields echoed into the passkey ceremony
- Exploit idea: Swap the wallet object between the two awaits.
- Invariant to test: Load and rebind must operate on the same wallet identity.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Integration test: mutate the wallet between the awaits of MfaPasskeyApi.generateAuthenticationOptions and assert the operation aborts.
