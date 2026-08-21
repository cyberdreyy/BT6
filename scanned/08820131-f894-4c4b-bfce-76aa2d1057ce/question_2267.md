# Q2267: no response signature verification in get-wallet.ts

## Question
The wallet-api response is consumed after only a method-name comparison; can an attacker return a response through getWallet(): WalletGet by wallet_id whose signature field is arbitrary and have it used or broadcast?

## Target
- File/function: [src/wallet-api/get-wallet.ts](src/wallet-api/get-wallet.ts) - getWallet(): WalletGet by wallet_id, returns additional_signers
- Entrypoint: addSessionSigners read-modify-write
- Attacker controls: wallet_id value and the returned additional_signers list used for the next write
- Exploit idea: Return an arbitrary signature and observe it flowing to the caller.
- Invariant to test: Responses carrying signatures must be verified against the request and the wallet key.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: return a bogus signature from getWallet(): WalletGet by wallet_id's route and assert verification fails.
