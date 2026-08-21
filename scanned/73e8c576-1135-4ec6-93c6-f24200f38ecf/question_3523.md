# Q3523: oauth_tokens emitted to any listener in PhoneApi.ts

## Question
Provider tokens from PhoneApi.sendCode are emitted through the session 'oauth_tokens_granted' event to every registered listener; can an attacker register or keep a listener that receives another flow's provider tokens?

## Target
- File/function: [src/client/auth/PhoneApi.ts](src/client/auth/PhoneApi.ts) - PhoneApi.sendCode, loginWithCode, linkWithCode, updatePhone, unlink
- Entrypoint: privy.auth.phone.loginWithCode(phone, code)
- Attacker controls: phoneNumber string (unnormalized), code, mode, opts.embedded
- Exploit idea: Attach a listener, trigger an unrelated login flow, and observe the tokens delivered.
- Invariant to test: Provider tokens must only reach the flow that requested them.
- Expected Immunefi impact: Critical - retrieval of sensitive user data: session/identity/provider tokens, key-material handles, or entropy identifiers reach a party that must not hold them.
- Fast validation: Unit test: attach a listener, run an unrelated PhoneApi.sendCode flow and assert the listener is not invoked.
