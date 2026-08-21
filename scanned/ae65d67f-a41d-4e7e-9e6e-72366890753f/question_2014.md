# Q2014: icloud configuration drives recovery choice in MfaPasskeyApi.ts

## Question
RecoveryICloudApi.getICloudConfiguration returns configuration consumed as trusted; can an attacker influence the returned configuration so MfaPasskeyApi.generateAuthenticationOptions performs recovery against an attacker-chosen record?

## Target
- File/function: [src/client/mfa/MfaPasskeyApi.ts](src/client/mfa/MfaPasskeyApi.ts) - MfaPasskeyApi.generateAuthenticationOptions
- Entrypoint: privy.mfa.passkey.generateAuthenticationOptions(input)
- Attacker controls: relying party and options fields echoed into the passkey ceremony
- Exploit idea: Return a configuration naming a foreign record name and observe the recovery attempt.
- Invariant to test: Recovery targets must be bound to the authenticated user's own records.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: return a foreign record configuration and assert MfaPasskeyApi.generateAuthenticationOptions refuses to use it.
