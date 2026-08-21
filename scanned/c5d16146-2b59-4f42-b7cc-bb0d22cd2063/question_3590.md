# Q3590: wallet-api errors surface raw responses in sign-wallet-request.ts

## Question
Errors from these routes are wrapped with code and error text; can an attacker trigger an error through SignWalletRequest signer indirection (proxy.signWithUserSigner with accessToken) whose message discloses another user's wallet identifiers?

## Target
- File/function: [src/wallet-api/sign-wallet-request.ts](src/wallet-api/sign-wallet-request.ts) - SignWalletRequest signer indirection (proxy.signWithUserSigner with accessToken)
- Entrypoint: every wallet-api signature
- Attacker controls: which message string is handed to the user signer and what it commits to
- Exploit idea: Force error responses and inspect the propagated text.
- Invariant to test: Error text must not disclose identifiers of resources the caller does not own.
- Expected Immunefi impact: Critical - retrieval of sensitive user data: session/identity/provider tokens, key-material handles, or entropy identifiers reach a party that must not hold them.
- Fast validation: Unit test: force a 403 in SignWalletRequest signer indirection (proxy.signWithUserSigner with accessToken) and assert the surfaced error carries no foreign identifiers.
