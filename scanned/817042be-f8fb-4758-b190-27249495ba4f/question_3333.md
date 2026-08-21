# Q3333: analytics record recovery details in MfaSmsApi.ts

## Question
setRecovery emits analytics containing address, target and existing recovery methods; can an attacker use MfaSmsApi.sendCode to learn another user's recovery configuration through those payloads?

## Target
- File/function: [src/client/mfa/MfaSmsApi.ts](src/client/mfa/MfaSmsApi.ts) - MfaSmsApi.sendCode
- Entrypoint: privy.mfa.sms.sendCode(input)
- Attacker controls: phone/target fields in the input body, repetition
- Exploit idea: Trigger the events and inspect what leaves the device.
- Invariant to test: Recovery configuration must not be exported in analytics payloads.
- Expected Immunefi impact: Critical - retrieval of sensitive user data: session/identity/provider tokens, key-material handles, or entropy identifiers reach a party that must not hold them.
- Fast validation: Unit test: capture analytics during MfaSmsApi.sendCode and assert no recovery method or address is included.
