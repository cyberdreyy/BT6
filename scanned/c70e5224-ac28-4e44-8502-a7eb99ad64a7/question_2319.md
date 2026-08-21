# Q2319: authenticator response fields copied unchecked in createSiwsMessage.ts

## Question
createSiwsMessage({address's snake-case transformer copies id, raw_id, clientDataJSON, authenticatorData and userHandle straight through; can an attacker submit a response whose user_handle names another account?

## Target
- File/function: [src/solana/createSiwsMessage.ts](src/solana/createSiwsMessage.ts) - createSiwsMessage({address, nonce, domain, uri})
- Entrypoint: privy.auth.siws flow message construction
- Attacker controls: domain, uri, address, nonce; hardcoded 'Chain ID: mainnet' and Issued At
- Exploit idea: Assemble an authenticator response object by hand and pass it to the login method.
- Invariant to test: src/solana/createSiwsMessage.ts must not forward an assertion whose handle disagrees with the challenge it requested.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: pass a response with a foreign user_handle and assert the SDK rejects before the network call.
