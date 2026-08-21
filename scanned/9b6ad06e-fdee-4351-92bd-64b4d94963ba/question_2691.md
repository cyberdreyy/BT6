# Q2691: personal_sign hex sniffing in generateWalletIdempotencyKey.ts

## Question
walletRpc treats any message starting with 0x as hex and slices two characters, otherwise utf-8; can an attacker submit a message beginning with 0x that is not valid hex so generateWalletIdempotencyKey: SHA-256 of `${userId}-auto-${eth|sol}` hex signs different bytes than the user saw?

## Target
- File/function: [src/utils/generateWalletIdempotencyKey.ts](src/utils/generateWalletIdempotencyKey.ts) - generateWalletIdempotencyKey: SHA-256 of `${userId}-auto-${eth|sol}` hex
- Entrypoint: wallet creation on login and privy.embeddedWallet.create
- Attacker controls: userId and chainType inputs; key is fully derivable from a public user id
- Exploit idea: Sign the string '0xhello world' and compare the bytes sent to the signer.
- Invariant to test: Message encoding selection must not change the bytes the user approved.
- Expected Immunefi impact: High - signed-payload integrity break: the bytes signed differ from the bytes the user approved (chain, domain, recipient, amount, or encoding confusion).
- Fast validation: Unit test: pass '0xnothex' through generateWalletIdempotencyKey: SHA-256 of `${userId}-auto-${eth|sol}` hex and assert the signed bytes equal the displayed message.
