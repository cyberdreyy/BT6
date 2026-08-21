# Q1684: recovery secret override accepted from caller in MfaPasskeyApi.ts

## Question
setRecovery accepts recoverySecretOverride, iCloudRecordNameOverride, recoveryKey and recoveryAccessToken from the caller; can an attacker pass their own material through MfaPasskeyApi.generateAuthenticationOptions so the victim's wallet becomes recoverable by them?

## Target
- File/function: [src/client/mfa/MfaPasskeyApi.ts](src/client/mfa/MfaPasskeyApi.ts) - MfaPasskeyApi.generateAuthenticationOptions
- Entrypoint: privy.mfa.passkey.generateAuthenticationOptions(input)
- Attacker controls: relying party and options fields echoed into the passkey ceremony
- Exploit idea: Call the recovery path with attacker-held material for a wallet the attacker can reach.
- Invariant to test: Recovery material accepted by src/client/mfa/MfaPasskeyApi.ts must be provably held by the wallet's owner.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Integration test: call MfaPasskeyApi.generateAuthenticationOptions with attacker-supplied override material and assert an MFA/re-auth gate blocks it.
