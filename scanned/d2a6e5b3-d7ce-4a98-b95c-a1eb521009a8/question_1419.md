# Q1419: kamino_withdraw: withdraw releases more value than the external position burned [a-user-with-partially-funded] [recipient-binding]

## Question
Can an unprivileged attacker call `kamino_withdraw` with a user with partially funded external position state already present so `kamino_withdraw` releases more value than the corresponding external position actually burned, violating `Kamino withdraw must release only value actually redeemed from the exact obligation/reserve pair and correct recipient` and causing `Critical: direct theft or phantom redemption`? Focus specifically on destination binding across intermediary ATAs, vault authorities, and final transfer-out.

## Target
- File/function: `programs/marginfi/src/instructions/kamino/withdraw.rs` / `kamino_withdraw`
- Entrypoint: `kamino_withdraw`
- Attacker controls: a user with partially funded external position state already present
- Exploit idea: Audit redeem/share conversions, rounding, and cached external balances around withdraw flows. Focus specifically on destination binding across intermediary ATAs, vault authorities, and final transfer-out.
- Invariant to test: Kamino withdraw must release only value actually redeemed from the exact obligation/reserve pair and correct recipient
- Expected Immunefi impact: Critical: direct theft or phantom redemption
- Fast validation: Compare external burned shares/assets to internal released value under adversarial amounts and assert no excess release occurs. Swap every plausible destination and assert no accepted path releases value to an unvalidated recipient.
