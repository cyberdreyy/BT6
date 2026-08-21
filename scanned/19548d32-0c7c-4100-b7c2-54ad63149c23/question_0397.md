# Q0397: canonicalize drops undefined fields in get-wallet.ts

## Question
generateAuthorizationSignature canonicalises the payload with canonicalize(), which omits undefined values and cannot represent them; can an attacker craft two semantically different payloads that canonicalise identically and reuse one signature for the other through getWallet(): WalletGet by wallet_id?

## Target
- File/function: [src/wallet-api/get-wallet.ts](src/wallet-api/get-wallet.ts) - getWallet(): WalletGet by wallet_id, returns additional_signers
- Entrypoint: addSessionSigners read-modify-write
- Attacker controls: wallet_id value and the returned additional_signers list used for the next write
- Exploit idea: Build payloads differing only by undefined-valued or key-ordered fields and compare the canonical strings.
- Invariant to test: Canonicalisation must be injective over the payloads it authorises.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: assert getWallet(): WalletGet by wallet_id produces distinct signatures for semantically distinct payloads.
