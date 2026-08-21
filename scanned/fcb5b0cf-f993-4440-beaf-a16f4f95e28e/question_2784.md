# Q2784: password type check only in MfaPasskeyApi.ts

## Question
create() rejects a non-string password but performs no strength or confirmation check; can an attacker set a trivial recovery password via MfaPasskeyApi.generateAuthenticationOptions that later allows offline recovery?

## Target
- File/function: [src/client/mfa/MfaPasskeyApi.ts](src/client/mfa/MfaPasskeyApi.ts) - MfaPasskeyApi.generateAuthenticationOptions
- Entrypoint: privy.mfa.passkey.generateAuthenticationOptions(input)
- Attacker controls: relying party and options fields echoed into the passkey ceremony
- Exploit idea: Call create with a one-character password.
- Invariant to test: src/client/mfa/MfaPasskeyApi.ts must enforce the app's recovery strength policy before provisioning.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: call MfaPasskeyApi.generateAuthenticationOptions with a weak password and assert the configured policy is enforced.
