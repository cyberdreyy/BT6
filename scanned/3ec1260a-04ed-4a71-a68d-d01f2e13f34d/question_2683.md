# Q2683: personal_sign hex sniffing in walletRpc.ts

## Question
walletRpc treats any message starting with 0x as hex and slices two characters, otherwise utf-8; can an attacker submit a message beginning with 0x that is not valid hex so handleWalletApiRpc signs different bytes than the user saw?

## Target
- File/function: [src/embedded/stack/walletRpc.ts](src/embedded/stack/walletRpc.ts) - handleWalletApiRpc, handleEthereumRpc, handleSolanaRpc (method-name echo checks like i.method !== 'personal_sign')
- Entrypoint: provider.request({method, params}) on a TEE (privy-v2) wallet
- Attacker controls: method string, params array contents, response method/data fields
- Exploit idea: Sign the string '0xhello world' and compare the bytes sent to the signer.
- Invariant to test: Message encoding selection must not change the bytes the user approved.
- Expected Immunefi impact: High - signed-payload integrity break: the bytes signed differ from the bytes the user approved (chain, domain, recipient, amount, or encoding confusion).
- Fast validation: Unit test: pass '0xnothex' through handleWalletApiRpc and assert the signed bytes equal the displayed message.
