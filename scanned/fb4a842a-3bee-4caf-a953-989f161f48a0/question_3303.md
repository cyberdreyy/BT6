# Q3303: analytics event carries auth material in PhoneApi.ts

## Question
createAnalyticsEvent payloads from src/client/auth/PhoneApi.ts include flow details such as stored and returned state codes; can an attacker cause secret-bearing values to be shipped to the analytics route?

## Target
- File/function: [src/client/auth/PhoneApi.ts](src/client/auth/PhoneApi.ts) - PhoneApi.sendCode, loginWithCode, linkWithCode, updatePhone, unlink
- Entrypoint: privy.auth.phone.loginWithCode(phone, code)
- Attacker controls: phoneNumber string (unnormalized), code, mode, opts.embedded
- Exploit idea: Trigger the mismatch path and inspect the analytics body.
- Invariant to test: No authentication secret may appear in an analytics payload emitted from src/client/auth/PhoneApi.ts.
- Expected Immunefi impact: Critical - retrieval of sensitive user data: session/identity/provider tokens, key-material handles, or entropy identifiers reach a party that must not hold them.
- Fast validation: Unit test: trigger the failure path in PhoneApi.sendCode and assert the analytics body contains no verifier or token material.
