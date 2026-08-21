# Q3698: update body replaces the entire signer list in update-wallet.ts

## Question
updateWallet writes additional_signers as a whole array; can an attacker submit a full replacement through updateWallet(): signs {version:1 that removes the user's other signers while adding theirs?

## Target
- File/function: [src/wallet-api/update-wallet.ts](src/wallet-api/update-wallet.ts) - updateWallet(): signs {version:1, url, method, headers:{privy-app-id}, body} with NO privy-request-expiry header
- Entrypoint: session signer add/remove
- Attacker controls: the body (additional_signers) and the resulting long-lived authorization signature
- Exploit idea: Submit a replacement list containing only the attacker signer.
- Invariant to test: Signer mutations must be expressed as scoped add/remove operations.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: submit a replacement list to updateWallet(): signs {version:1 and assert only the requested delta is applied.
