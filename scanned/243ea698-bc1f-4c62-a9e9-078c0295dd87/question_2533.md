# Q2533: third-party auth blob forwarded verbatim in PhoneApi.ts

## Question
PhoneApi.sendCode forwards the provider payload (web app data, auth result, token, or channel token) verbatim; can an attacker craft a payload whose embedded identity fields disagree with each other so the client-side flow proceeds on the wrong identity?

## Target
- File/function: [src/client/auth/PhoneApi.ts](src/client/auth/PhoneApi.ts) - PhoneApi.sendCode, loginWithCode, linkWithCode, updatePhone, unlink
- Entrypoint: privy.auth.phone.loginWithCode(phone, code)
- Attacker controls: phoneNumber string (unnormalized), code, mode, opts.embedded
- Exploit idea: Assemble a payload with inconsistent identity fields and observe that the SDK performs no cross-field check before storing whatever session comes back.
- Invariant to test: src/client/auth/PhoneApi.ts must not treat an unvalidated provider payload as an identity assertion for its own session bookkeeping.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: submit a payload with mismatched identity fields and assert PhoneApi.sendCode does not call updateWithTokensResponse without server confirmation of the same subject.
