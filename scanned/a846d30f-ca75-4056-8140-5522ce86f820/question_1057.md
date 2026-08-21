# Q1057: update-wallet envelope carries no expiry in get-wallet.ts

## Question
updateWallet signs {version, url, method, headers:{privy-app-id}, body} with no privy-request-expiry; can an attacker capture that signature through getWallet(): WalletGet by wallet_id and replay the signer-set change indefinitely?

## Target
- File/function: [src/wallet-api/get-wallet.ts](src/wallet-api/get-wallet.ts) - getWallet(): WalletGet by wallet_id, returns additional_signers
- Entrypoint: addSessionSigners read-modify-write
- Attacker controls: wallet_id value and the returned additional_signers list used for the next write
- Exploit idea: Capture the authorization signature from a session-signer update and replay it later.
- Invariant to test: Every authorization signature must be time-bounded and single-use.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Integration test: replay a captured update signature via getWallet(): WalletGet by wallet_id and assert rejection.
