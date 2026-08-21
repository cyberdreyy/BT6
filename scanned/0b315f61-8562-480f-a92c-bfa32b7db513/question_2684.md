# Q2684: personal_sign hex sniffing in walletCreate.ts

## Question
walletRpc treats any message starting with 0x as hex and slices two characters, otherwise utf-8; can an attacker submit a message beginning with 0x that is not valid hex so createWalletApiWallet signs different bytes than the user saw?

## Target
- File/function: [src/embedded/stack/walletCreate.ts](src/embedded/stack/walletCreate.ts) - createWalletApiWallet, create (privy-idempotency-key header)
- Entrypoint: privy.embeddedWallet.create({idempotencyKey}) in user-controlled-server-wallets-only mode
- Attacker controls: idempotencyKey string, chainType, repeated concurrent creates
- Exploit idea: Sign the string '0xhello world' and compare the bytes sent to the signer.
- Invariant to test: Message encoding selection must not change the bytes the user approved.
- Expected Immunefi impact: High - signed-payload integrity break: the bytes signed differ from the bytes the user approved (chain, domain, recipient, amount, or encoding confusion).
- Fast validation: Unit test: pass '0xnothex' through createWalletApiWallet and assert the signed bytes equal the displayed message.
