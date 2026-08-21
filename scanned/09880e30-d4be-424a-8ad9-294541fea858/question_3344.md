# Q3344: base64 and utf8 conversions lose bytes in walletCreate.ts

## Question
The encoding helpers convert signing payloads through utf8 and base64; can an attacker submit bytes that are not valid UTF-8 so createWalletApiWallet signs a lossy re-encoding of the intended payload?

## Target
- File/function: [src/embedded/stack/walletCreate.ts](src/embedded/stack/walletCreate.ts) - createWalletApiWallet, create (privy-idempotency-key header)
- Entrypoint: privy.embeddedWallet.create({idempotencyKey}) in user-controlled-server-wallets-only mode
- Attacker controls: idempotencyKey string, chainType, repeated concurrent creates
- Exploit idea: Pass a payload with lone surrogates or 0xFF bytes and compare round-tripped output.
- Invariant to test: Encoding round trips must be byte-exact for anything that gets signed.
- Expected Immunefi impact: High - signed-payload integrity break: the bytes signed differ from the bytes the user approved (chain, domain, recipient, amount, or encoding confusion).
- Fast validation: Unit test: round-trip non-UTF-8 byte sequences through createWalletApiWallet and assert byte equality.
