# Q2183: juplend_withdraw: withdraw releases more value than the external position burned [remaining-accounts-that-can-swap] [recipient-binding]

## Question
Can an unprivileged attacker call `juplend_withdraw` with remaining accounts that can swap rate update and withdraw market contexts so `juplend_withdraw` releases more value than the corresponding external position actually burned, violating `Juplend withdraw must burn the right supply position and release only the value actually redeemed to the correct recipient` and causing `Critical: direct theft or phantom redemption`? Focus specifically on destination binding across intermediary ATAs, vault authorities, and final transfer-out.

## Target
- File/function: `programs/marginfi/src/instructions/juplend/withdraw.rs` / `juplend_withdraw`
- Entrypoint: `juplend_withdraw`
- Attacker controls: remaining accounts that can swap rate update and withdraw market contexts
- Exploit idea: Audit redeem/share conversions, rounding, and cached external balances around withdraw flows. Focus specifically on destination binding across intermediary ATAs, vault authorities, and final transfer-out.
- Invariant to test: Juplend withdraw must burn the right supply position and release only the value actually redeemed to the correct recipient
- Expected Immunefi impact: Critical: direct theft or phantom redemption
- Fast validation: Compare external burned shares/assets to internal released value under adversarial amounts and assert no excess release occurs. Swap every plausible destination and assert no accepted path releases value to an unvalidated recipient.
