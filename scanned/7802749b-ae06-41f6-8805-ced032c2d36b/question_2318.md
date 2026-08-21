# Q2318: authenticator response fields copied unchecked in SiwsApi.ts

## Question
SiwsApi.fetchNonce's snake-case transformer copies id, raw_id, clientDataJSON, authenticatorData and userHandle straight through; can an attacker submit a response whose user_handle names another account?

## Target
- File/function: [src/client/auth/SiwsApi.ts](src/client/auth/SiwsApi.ts) - SiwsApi.fetchNonce, login, link, unlink
- Entrypoint: privy.auth.siws.login({message, signature, walletClientType, connectorType, mode})
- Attacker controls: message string, signature, wallet metadata, nonce reuse
- Exploit idea: Assemble an authenticator response object by hand and pass it to the login method.
- Invariant to test: src/client/auth/SiwsApi.ts must not forward an assertion whose handle disagrees with the challenge it requested.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: pass a response with a foreign user_handle and assert the SDK rejects before the network call.
