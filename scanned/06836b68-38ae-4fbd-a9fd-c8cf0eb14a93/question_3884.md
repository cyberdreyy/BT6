# Q3884: recovery access token reused across providers in MfaPasskeyApi.ts

## Question
google-drive and icloud recovery both accept recoveryAccessToken; can an attacker present a token from one provider in the other's branch through MfaPasskeyApi.generateAuthenticationOptions?

## Target
- File/function: [src/client/mfa/MfaPasskeyApi.ts](src/client/mfa/MfaPasskeyApi.ts) - MfaPasskeyApi.generateAuthenticationOptions
- Entrypoint: privy.mfa.passkey.generateAuthenticationOptions(input)
- Attacker controls: relying party and options fields echoed into the passkey ceremony
- Exploit idea: Call recovery with a mismatched provider/token pair.
- Invariant to test: Recovery tokens must be validated against the provider the wallet is bound to.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: cross provider and token in MfaPasskeyApi.generateAuthenticationOptions and assert rejection.
