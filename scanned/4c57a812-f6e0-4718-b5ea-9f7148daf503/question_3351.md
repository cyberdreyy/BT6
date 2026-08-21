# Q3351: base64 and utf8 conversions lose bytes in generateWalletIdempotencyKey.ts

## Question
The encoding helpers convert signing payloads through utf8 and base64; can an attacker submit bytes that are not valid UTF-8 so generateWalletIdempotencyKey: SHA-256 of `${userId}-auto-${eth|sol}` hex signs a lossy re-encoding of the intended payload?

## Target
- File/function: [src/utils/generateWalletIdempotencyKey.ts](src/utils/generateWalletIdempotencyKey.ts) - generateWalletIdempotencyKey: SHA-256 of `${userId}-auto-${eth|sol}` hex
- Entrypoint: wallet creation on login and privy.embeddedWallet.create
- Attacker controls: userId and chainType inputs; key is fully derivable from a public user id
- Exploit idea: Pass a payload with lone surrogates or 0xFF bytes and compare round-tripped output.
- Invariant to test: Encoding round trips must be byte-exact for anything that gets signed.
- Expected Immunefi impact: High - signed-payload integrity break: the bytes signed differ from the bytes the user approved (chain, domain, recipient, amount, or encoding confusion).
- Fast validation: Unit test: round-trip non-UTF-8 byte sequences through generateWalletIdempotencyKey: SHA-256 of `${userId}-auto-${eth|sol}` hex and assert byte equality.
