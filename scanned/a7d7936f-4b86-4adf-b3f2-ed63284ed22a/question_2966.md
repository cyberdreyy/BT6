# Q2966: no ownership assertion in the helper in getUserSmartWallet.ts

## Question
getUserSmartWallet: first linked account of type smart_wallet filters the supplied user object without asserting the object came from an authenticated read; can an attacker pass a fabricated user so the helper returns an account they control?

## Target
- File/function: [src/utils/getUserSmartWallet.ts](src/utils/getUserSmartWallet.ts) - getUserSmartWallet: first linked account of type smart_wallet
- Entrypoint: smart-wallet routing and linking
- Attacker controls: linked_accounts contents including multiple smart wallets
- Exploit idea: Pass a hand-built user object.
- Invariant to test: Helpers that select signing accounts must require server-confirmed input.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: pass a fabricated user to getUserSmartWallet: first linked account of type smart_wallet and assert the caller re-validates.
