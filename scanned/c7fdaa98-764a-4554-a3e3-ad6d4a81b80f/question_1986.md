# Q1986: chain id normalisation strips context in PasskeyApi.ts

## Question
The chainId is normalised with replace('eip155:',''); can an attacker supply a chainId form that survives normalisation and makes the signed message describe a different chain than the one bound server-side?

## Target
- File/function: [src/client/auth/PasskeyApi.ts](src/client/auth/PasskeyApi.ts) - PasskeyApi.generateAuthenticationOptions, loginWithPasskey, signupWithPasskey, linkWithPasskey, _transformAuthenticationResponseToSnakeCase
- Entrypoint: privy.auth.passkey.loginWithPasskey(response, challenge, relyingParty)
- Attacker controls: relyingParty string, challenge, authenticator response object fields
- Exploit idea: Pass chainId values such as 'eip155:eip155:1' or '01' and inspect the resulting message.
- Invariant to test: Chain identity in the authentication message must be canonical and unambiguous.
- Expected Immunefi impact: High - signed-payload integrity break: the bytes signed differ from the bytes the user approved (chain, domain, recipient, amount, or encoding confusion).
- Fast validation: Unit test: feed a table of chainId encodings to PasskeyApi.generateAuthenticationOptions and assert a single canonical output or a rejection.
