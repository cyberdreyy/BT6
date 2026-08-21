# Q2710: params object forwarded verbatim in sign-wallet-request.ts

## Question
The params branch of the signed body is passed through unvalidated; can an attacker include extra params fields through SignWalletRequest signer indirection (proxy.signWithUserSigner with accessToken) that the server honours but the client never showed the user?

## Target
- File/function: [src/wallet-api/sign-wallet-request.ts](src/wallet-api/sign-wallet-request.ts) - SignWalletRequest signer indirection (proxy.signWithUserSigner with accessToken)
- Entrypoint: every wallet-api signature
- Attacker controls: which message string is handed to the user signer and what it commits to
- Exploit idea: Add unexpected keys to the params object.
- Invariant to test: Only a validated params schema may be signed.
- Expected Immunefi impact: High - signed-payload integrity break: the bytes signed differ from the bytes the user approved (chain, domain, recipient, amount, or encoding confusion).
- Fast validation: Unit test: add unknown params keys in SignWalletRequest signer indirection (proxy.signWithUserSigner with accessToken) and assert they are stripped or rejected.
