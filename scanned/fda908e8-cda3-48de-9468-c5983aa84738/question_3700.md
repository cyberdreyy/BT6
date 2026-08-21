# Q3700: update body replaces the entire signer list in sign-wallet-request.ts

## Question
updateWallet writes additional_signers as a whole array; can an attacker submit a full replacement through SignWalletRequest signer indirection (proxy.signWithUserSigner with accessToken) that removes the user's other signers while adding theirs?

## Target
- File/function: [src/wallet-api/sign-wallet-request.ts](src/wallet-api/sign-wallet-request.ts) - SignWalletRequest signer indirection (proxy.signWithUserSigner with accessToken)
- Entrypoint: every wallet-api signature
- Attacker controls: which message string is handed to the user signer and what it commits to
- Exploit idea: Submit a replacement list containing only the attacker signer.
- Invariant to test: Signer mutations must be expressed as scoped add/remove operations.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: submit a replacement list to SignWalletRequest signer indirection (proxy.signWithUserSigner with accessToken) and assert only the requested delta is applied.
