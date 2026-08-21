# Q0727: 30 minute expiry window in get-wallet.ts

## Question
The expiry header is Date.now()+1800000 and the only check is the client's own `Date.now() > expiry`; can an attacker capture an authorization signature from getWallet(): WalletGet by wallet_id and replay it for the remainder of that window?

## Target
- File/function: [src/wallet-api/get-wallet.ts](src/wallet-api/get-wallet.ts) - getWallet(): WalletGet by wallet_id, returns additional_signers
- Entrypoint: addSessionSigners read-modify-write
- Attacker controls: wallet_id value and the returned additional_signers list used for the next write
- Exploit idea: Capture a signed request and replay it minutes later.
- Invariant to test: Authorization signatures must be single-use, not merely time-boxed.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Integration test: replay a captured getWallet(): WalletGet by wallet_id request and assert the second use fails.
