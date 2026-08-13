# Q1513: kamino_withdraw: withdraw path leaves internal debt/value view stale after CPI [a-withdraw-immediately-after-reward] [recipient-binding]

## Question
Can an unprivileged attacker call `kamino_withdraw` with a withdraw immediately after reward harvest or price-cache update so `kamino_withdraw` completes the external CPI but leaves internal value view stale, breaking `Kamino withdraw must release only value actually redeemed from the exact obligation/reserve pair and correct recipient` and causing `Critical: direct theft or phantom redemption`? Focus specifically on destination binding across intermediary ATAs, vault authorities, and final transfer-out.

## Target
- File/function: `programs/marginfi/src/instructions/kamino/withdraw.rs` / `kamino_withdraw`
- Entrypoint: `kamino_withdraw`
- Attacker controls: a withdraw immediately after reward harvest or price-cache update
- Exploit idea: Audit whether post-withdraw internal state, caches, and share accounting are refreshed from the exact redeemed value. Focus specifically on destination binding across intermediary ATAs, vault authorities, and final transfer-out.
- Invariant to test: Kamino withdraw must release only value actually redeemed from the exact obligation/reserve pair and correct recipient
- Expected Immunefi impact: Critical: direct theft or phantom redemption
- Fast validation: After controlled withdraws, immediately try dependent borrow/withdraw paths and assert the internal value view matches the external post-state. Swap every plausible destination and assert no accepted path releases value to an unvalidated recipient.
