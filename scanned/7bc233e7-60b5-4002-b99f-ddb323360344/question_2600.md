# Q2600: chain_type chosen by the caller in sign-wallet-request.ts

## Question
The signed body includes a caller-supplied chain_type; can an attacker mismatch chain_type against the wallet through SignWalletRequest signer indirection (proxy.signWithUserSigner with accessToken) so a signature valid on one chain is produced for a wallet on another?

## Target
- File/function: [src/wallet-api/sign-wallet-request.ts](src/wallet-api/sign-wallet-request.ts) - SignWalletRequest signer indirection (proxy.signWithUserSigner with accessToken)
- Entrypoint: every wallet-api signature
- Attacker controls: which message string is handed to the user signer and what it commits to
- Exploit idea: Submit an ethereum method for a solana wallet id.
- Invariant to test: Chain type must be derived from the wallet record, not the request.
- Expected Immunefi impact: High - signed-payload integrity break: the bytes signed differ from the bytes the user approved (chain, domain, recipient, amount, or encoding confusion).
- Fast validation: Unit test: mismatch chain_type and wallet in SignWalletRequest signer indirection (proxy.signWithUserSigner with accessToken) and assert rejection.
