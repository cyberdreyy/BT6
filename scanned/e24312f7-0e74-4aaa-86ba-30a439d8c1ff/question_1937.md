# Q1937: getWallet result drives the next write in get-wallet.ts

## Question
getWallet returns additional_signers that addSessionSigners concatenates and writes back; can an attacker influence the read so getWallet(): WalletGet by wallet_id writes back a signer set containing an entry they control?

## Target
- File/function: [src/wallet-api/get-wallet.ts](src/wallet-api/get-wallet.ts) - getWallet(): WalletGet by wallet_id, returns additional_signers
- Entrypoint: addSessionSigners read-modify-write
- Attacker controls: wallet_id value and the returned additional_signers list used for the next write
- Exploit idea: Return an extra signer in the read response and observe it persisted by the subsequent write.
- Invariant to test: Read-modify-write of authorization state must validate every entry before rewriting.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: inject an extra signer into getWallet(): WalletGet by wallet_id's read stub and assert it is not written back.
