# Q2702: bitcoin message decoded as UTF-8 in smart-wallets.ts

## Question
EmbeddedBitcoinWalletProvider.sign decodes the message bytes with TextDecoder('utf8') before sending; can an attacker submit non-UTF-8 bytes so smart-wallets entry (BICONOMY signs a replacement-character-mangled message?

## Target
- File/function: [src/smart-wallets.ts](src/smart-wallets.ts) - smart-wallets entry (BICONOMY, COINBASE_SMART_WALLET, KERNEL, LIGHT_ACCOUNT, SAFE, THIRDWEB, NEXUS)
- Entrypoint: import {...} from '@privy-io/js-sdk-core/smart-wallets'
- Attacker controls: smart wallet type/version strings used for linking and routing
- Exploit idea: Pass bytes containing 0x80-0xFF sequences and compare what is signed.
- Invariant to test: Message bytes must reach the signer unmodified.
- Expected Immunefi impact: High - signed-payload integrity break: the bytes signed differ from the bytes the user approved (chain, domain, recipient, amount, or encoding confusion).
- Fast validation: Unit test: pass invalid UTF-8 through smart-wallets entry (BICONOMY and assert byte-exact signing or rejection.
