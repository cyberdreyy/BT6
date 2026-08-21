# Q3334: analytics record recovery details in MfaPasskeyApi.ts

## Question
setRecovery emits analytics containing address, target and existing recovery methods; can an attacker use MfaPasskeyApi.generateAuthenticationOptions to learn another user's recovery configuration through those payloads?

## Target
- File/function: [src/client/mfa/MfaPasskeyApi.ts](src/client/mfa/MfaPasskeyApi.ts) - MfaPasskeyApi.generateAuthenticationOptions
- Entrypoint: privy.mfa.passkey.generateAuthenticationOptions(input)
- Attacker controls: relying party and options fields echoed into the passkey ceremony
- Exploit idea: Trigger the events and inspect what leaves the device.
- Invariant to test: Recovery configuration must not be exported in analytics payloads.
- Expected Immunefi impact: Critical - retrieval of sensitive user data: session/identity/provider tokens, key-material handles, or entropy identifiers reach a party that must not hold them.
- Fast validation: Unit test: capture analytics during MfaPasskeyApi.generateAuthenticationOptions and assert no recovery method or address is included.
