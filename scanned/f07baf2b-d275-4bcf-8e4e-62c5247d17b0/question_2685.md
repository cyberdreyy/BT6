# Q2685: personal_sign hex sniffing in session-signers.ts

## Question
walletRpc treats any message starting with 0x as hex and slices two characters, otherwise utf-8; can an attacker submit a message beginning with 0x that is not valid hex so addSessionSigners (getWallet then updateWallet with additional_signers.concat) signs different bytes than the user saw?

## Target
- File/function: [src/embedded/stack/session-signers.ts](src/embedded/stack/session-signers.ts) - addSessionSigners (getWallet then updateWallet with additional_signers.concat), removeSessionSigners
- Entrypoint: privy.embeddedWallet session-signer flows
- Attacker controls: signers array contents, concurrency against another add/remove, wallet object fields
- Exploit idea: Sign the string '0xhello world' and compare the bytes sent to the signer.
- Invariant to test: Message encoding selection must not change the bytes the user approved.
- Expected Immunefi impact: High - signed-payload integrity break: the bytes signed differ from the bytes the user approved (chain, domain, recipient, amount, or encoding confusion).
- Fast validation: Unit test: pass '0xnothex' through addSessionSigners (getWallet then updateWallet with additional_signers.concat) and assert the signed bytes equal the displayed message.
