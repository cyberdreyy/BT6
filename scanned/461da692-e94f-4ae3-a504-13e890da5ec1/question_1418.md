# Q1418: kamino_withdraw: withdraw releases more value than the external position burned [a-withdraw-immediately-after-reward] [round-trip]

## Question
Can an unprivileged attacker call `kamino_withdraw` with a withdraw immediately after reward harvest or price-cache update so `kamino_withdraw` releases more value than the corresponding external position actually burned, violating `Kamino withdraw must release only value actually redeemed from the exact obligation/reserve pair and correct recipient` and causing `Critical: direct theft or phantom redemption`? Focus specifically on withdraw/deposit round-trips around the integration boundary for asymmetric conversions.

## Target
- File/function: `programs/marginfi/src/instructions/kamino/withdraw.rs` / `kamino_withdraw`
- Entrypoint: `kamino_withdraw`
- Attacker controls: a withdraw immediately after reward harvest or price-cache update
- Exploit idea: Audit redeem/share conversions, rounding, and cached external balances around withdraw flows. Focus specifically on withdraw/deposit round-trips around the integration boundary for asymmetric conversions.
- Invariant to test: Kamino withdraw must release only value actually redeemed from the exact obligation/reserve pair and correct recipient
- Expected Immunefi impact: Critical: direct theft or phantom redemption
- Fast validation: Compare external burned shares/assets to internal released value under adversarial amounts and assert no excess release occurs. Run deposit-then-withdraw and withdraw-then-deposit loops and assert no positive attacker value emerges.
