# Q3587: wallet-api errors surface raw responses in get-wallet.ts

## Question
Errors from these routes are wrapped with code and error text; can an attacker trigger an error through getWallet(): WalletGet by wallet_id whose message discloses another user's wallet identifiers?

## Target
- File/function: [src/wallet-api/get-wallet.ts](src/wallet-api/get-wallet.ts) - getWallet(): WalletGet by wallet_id, returns additional_signers
- Entrypoint: addSessionSigners read-modify-write
- Attacker controls: wallet_id value and the returned additional_signers list used for the next write
- Exploit idea: Force error responses and inspect the propagated text.
- Invariant to test: Error text must not disclose identifiers of resources the caller does not own.
- Expected Immunefi impact: Critical - retrieval of sensitive user data: session/identity/provider tokens, key-material handles, or entropy identifiers reach a party that must not hold them.
- Fast validation: Unit test: force a 403 in getWallet(): WalletGet by wallet_id and assert the surfaced error carries no foreign identifiers.
