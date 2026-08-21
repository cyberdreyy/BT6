# Q2901: hex detection via loose regex in EmbeddedWalletProxy.ts

## Question
The hex predicate accepts any 0x-prefixed hex string of any length, including empty; can an attacker exploit that in EmbeddedWalletProxy.invoke (postMessage target '*') so a zero-length or odd-length value is passed to the signer?

## Target
- File/function: [src/embedded/EmbeddedWalletProxy.ts](src/embedded/EmbeddedWalletProxy.ts) - EmbeddedWalletProxy.invoke (postMessage target '*'), handleEmbeddedWalletMessages, invokeWithMfa, waitForReady, reload, ping, rpcWallet, signWithUserSigner, setRecovery, delegateWallets
- Entrypoint: privy.embeddedWallet.onMessage(msg) fed from the host page's message listener
- Attacker controls: the {id, event, data, error} object handed to onMessage, its arrival order and timing
- Exploit idea: Submit '0x' and an odd-length hex string.
- Invariant to test: Hex inputs must be length-validated before signing.
- Expected Immunefi impact: High - signed-payload integrity break: the bytes signed differ from the bytes the user approved (chain, domain, recipient, amount, or encoding confusion).
- Fast validation: Unit test: feed '0x' and odd-length values to EmbeddedWalletProxy.invoke (postMessage target '*') and assert rejection.
