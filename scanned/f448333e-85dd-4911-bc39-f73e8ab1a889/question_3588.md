# Q3588: wallet-api errors surface raw responses in update-wallet.ts

## Question
Errors from these routes are wrapped with code and error text; can an attacker trigger an error through updateWallet(): signs {version:1 whose message discloses another user's wallet identifiers?

## Target
- File/function: [src/wallet-api/update-wallet.ts](src/wallet-api/update-wallet.ts) - updateWallet(): signs {version:1, url, method, headers:{privy-app-id}, body} with NO privy-request-expiry header
- Entrypoint: session signer add/remove
- Attacker controls: the body (additional_signers) and the resulting long-lived authorization signature
- Exploit idea: Force error responses and inspect the propagated text.
- Invariant to test: Error text must not disclose identifiers of resources the caller does not own.
- Expected Immunefi impact: Critical - retrieval of sensitive user data: session/identity/provider tokens, key-material handles, or entropy identifiers reach a party that must not hold them.
- Fast validation: Unit test: force a 403 in updateWallet(): signs {version:1 and assert the surfaced error carries no foreign identifiers.
