# Q2313: authenticator response fields copied unchecked in PhoneApi.ts

## Question
PhoneApi.sendCode's snake-case transformer copies id, raw_id, clientDataJSON, authenticatorData and userHandle straight through; can an attacker submit a response whose user_handle names another account?

## Target
- File/function: [src/client/auth/PhoneApi.ts](src/client/auth/PhoneApi.ts) - PhoneApi.sendCode, loginWithCode, linkWithCode, updatePhone, unlink
- Entrypoint: privy.auth.phone.loginWithCode(phone, code)
- Attacker controls: phoneNumber string (unnormalized), code, mode, opts.embedded
- Exploit idea: Assemble an authenticator response object by hand and pass it to the login method.
- Invariant to test: src/client/auth/PhoneApi.ts must not forward an assertion whose handle disagrees with the challenge it requested.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: pass a response with a foreign user_handle and assert the SDK rejects before the network call.
