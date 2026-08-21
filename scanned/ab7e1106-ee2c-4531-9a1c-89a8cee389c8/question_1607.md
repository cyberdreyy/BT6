# Q1607: access token captured in the signing closure in get-wallet.ts

## Question
The signer closure captures the access token at construction; can an attacker keep a stale closure alive so getWallet(): WalletGet by wallet_id signs using a token belonging to a previous session?

## Target
- File/function: [src/wallet-api/get-wallet.ts](src/wallet-api/get-wallet.ts) - getWallet(): WalletGet by wallet_id, returns additional_signers
- Entrypoint: addSessionSigners read-modify-write
- Attacker controls: wallet_id value and the returned additional_signers list used for the next write
- Exploit idea: Obtain the closure, change sessions, then sign.
- Invariant to test: Signing must resolve the current session token at call time.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Integration test: change sessions and assert getWallet(): WalletGet by wallet_id refuses to reuse the captured token.
