# Q2911: hex detection via loose regex in generateWalletIdempotencyKey.ts

## Question
The hex predicate accepts any 0x-prefixed hex string of any length, including empty; can an attacker exploit that in generateWalletIdempotencyKey: SHA-256 of `${userId}-auto-${eth|sol}` hex so a zero-length or odd-length value is passed to the signer?

## Target
- File/function: [src/utils/generateWalletIdempotencyKey.ts](src/utils/generateWalletIdempotencyKey.ts) - generateWalletIdempotencyKey: SHA-256 of `${userId}-auto-${eth|sol}` hex
- Entrypoint: wallet creation on login and privy.embeddedWallet.create
- Attacker controls: userId and chainType inputs; key is fully derivable from a public user id
- Exploit idea: Submit '0x' and an odd-length hex string.
- Invariant to test: Hex inputs must be length-validated before signing.
- Expected Immunefi impact: High - signed-payload integrity break: the bytes signed differ from the bytes the user approved (chain, domain, recipient, amount, or encoding confusion).
- Fast validation: Unit test: feed '0x' and odd-length values to generateWalletIdempotencyKey: SHA-256 of `${userId}-auto-${eth|sol}` hex and assert rejection.
