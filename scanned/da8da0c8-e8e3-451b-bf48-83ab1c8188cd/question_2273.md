# Q2273: juplend_withdraw: withdraw path leaves internal debt/value view stale after CPI [a-supply-position-and-market] [recipient-binding]

## Question
Can an unprivileged attacker call `juplend_withdraw` with a supply position and market context from different users or markets so `juplend_withdraw` completes the external CPI but leaves internal value view stale, breaking `Juplend withdraw must burn the right supply position and release only the value actually redeemed to the correct recipient` and causing `Critical: direct theft or phantom redemption`? Focus specifically on destination binding across intermediary ATAs, vault authorities, and final transfer-out.

## Target
- File/function: `programs/marginfi/src/instructions/juplend/withdraw.rs` / `juplend_withdraw`
- Entrypoint: `juplend_withdraw`
- Attacker controls: a supply position and market context from different users or markets
- Exploit idea: Audit whether post-withdraw internal state, caches, and share accounting are refreshed from the exact redeemed value. Focus specifically on destination binding across intermediary ATAs, vault authorities, and final transfer-out.
- Invariant to test: Juplend withdraw must burn the right supply position and release only the value actually redeemed to the correct recipient
- Expected Immunefi impact: Critical: direct theft or phantom redemption
- Fast validation: After controlled withdraws, immediately try dependent borrow/withdraw paths and assert the internal value view matches the external post-state. Swap every plausible destination and assert no accepted path releases value to an unvalidated recipient.
