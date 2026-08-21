# Q2377: wallet-api path compiled from route templates in get-wallet.ts

## Question
getCompiledPath interpolates wallet_id into the route path before it is signed; can an attacker supply a wallet_id containing path separators so getWallet(): WalletGet by wallet_id signs and calls a different endpoint?

## Target
- File/function: [src/wallet-api/get-wallet.ts](src/wallet-api/get-wallet.ts) - getWallet(): WalletGet by wallet_id, returns additional_signers
- Entrypoint: addSessionSigners read-modify-write
- Attacker controls: wallet_id value and the returned additional_signers list used for the next write
- Exploit idea: Pass a wallet id containing '/' or '%2F'.
- Invariant to test: Path parameters must be encoded before compilation and signing.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: pass a separator-bearing wallet id to getWallet(): WalletGet by wallet_id and assert encoding or rejection.
