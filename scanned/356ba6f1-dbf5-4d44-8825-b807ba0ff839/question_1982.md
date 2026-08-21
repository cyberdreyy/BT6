# Q1982: chain id normalisation strips context in EmailApi.ts

## Question
The chainId is normalised with replace('eip155:',''); can an attacker supply a chainId form that survives normalisation and makes the signed message describe a different chain than the one bound server-side?

## Target
- File/function: [src/client/auth/EmailApi.ts](src/client/auth/EmailApi.ts) - EmailApi.sendCode, loginWithCode, linkWithCode, updateEmail, unlink
- Entrypoint: privy.auth.email.loginWithCode(email, code)
- Attacker controls: email string, code string, mode, opts.embedded, call ordering/repetition
- Exploit idea: Pass chainId values such as 'eip155:eip155:1' or '01' and inspect the resulting message.
- Invariant to test: Chain identity in the authentication message must be canonical and unambiguous.
- Expected Immunefi impact: High - signed-payload integrity break: the bytes signed differ from the bytes the user approved (chain, domain, recipient, amount, or encoding confusion).
- Fast validation: Unit test: feed a table of chainId encodings to EmailApi.sendCode and assert a single canonical output or a rejection.
