# Q2375: wallet-api path compiled from route templates in raw-sign.ts

## Question
getCompiledPath interpolates wallet_id into the route path before it is signed; can an attacker supply a wallet_id containing path separators so rawSign(): same expiry-signed envelope for WalletRawSign signs and calls a different endpoint?

## Target
- File/function: [src/wallet-api/raw-sign.ts](src/wallet-api/raw-sign.ts) - rawSign(): same expiry-signed envelope for WalletRawSign
- Entrypoint: raw-hash signing on an extended-chains wallet
- Attacker controls: the hash/params body fields and wallet_id
- Exploit idea: Pass a wallet id containing '/' or '%2F'.
- Invariant to test: Path parameters must be encoded before compilation and signing.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: pass a separator-bearing wallet id to rawSign(): same expiry-signed envelope for WalletRawSign and assert encoding or rejection.
