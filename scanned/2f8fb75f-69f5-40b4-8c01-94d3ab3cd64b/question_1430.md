# Q1430: kamino_withdraw: withdraw targets the wrong recipient or vault authority [same-slot-deposit-then-withdraw] [round-trip]

## Question
Can an unprivileged attacker use `kamino_withdraw` with same-slot deposit then withdraw around the same external position so `kamino_withdraw` sends withdrawn assets to the wrong recipient or through the wrong vault authority, breaking `Kamino withdraw must release only value actually redeemed from the exact obligation/reserve pair and correct recipient` and causing `Critical: direct theft or phantom redemption`? Focus specifically on withdraw/deposit round-trips around the integration boundary for asymmetric conversions.

## Target
- File/function: `programs/marginfi/src/instructions/kamino/withdraw.rs` / `kamino_withdraw`
- Entrypoint: `kamino_withdraw`
- Attacker controls: same-slot deposit then withdraw around the same external position
- Exploit idea: Probe destination binding and PDA authority checks across the final transfer-out phase. Focus specifically on withdraw/deposit round-trips around the integration boundary for asymmetric conversions.
- Invariant to test: Kamino withdraw must release only value actually redeemed from the exact obligation/reserve pair and correct recipient
- Expected Immunefi impact: Critical: direct theft or phantom redemption
- Fast validation: Swap destinations and authorities in the controlled setup and assert no accepted path transfers value to an unvalidated account. Run deposit-then-withdraw and withdraw-then-deposit loops and assert no positive attacker value emerges.
