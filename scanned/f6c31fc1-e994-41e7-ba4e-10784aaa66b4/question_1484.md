# Q1484: kamino_withdraw: withdraw accepts attacker-shaped optional accounts at closeout [a-user-with-partially-funded] [round-trip]

## Question
Can an unprivileged attacker use `kamino_withdraw` with a user with partially funded external position state already present so `kamino_withdraw` accepts attacker-shaped optional accounts during closeout, violating `Kamino withdraw must release only value actually redeemed from the exact obligation/reserve pair and correct recipient` and causing `Critical: direct theft or phantom redemption`? Focus specifically on withdraw/deposit round-trips around the integration boundary for asymmetric conversions.

## Target
- File/function: `programs/marginfi/src/instructions/kamino/withdraw.rs` / `kamino_withdraw`
- Entrypoint: `kamino_withdraw`
- Attacker controls: a user with partially funded external position state already present
- Exploit idea: Probe optional reward, mint, reserve, or destination accounts used only during withdraw and therefore easy to under-validate. Focus specifically on withdraw/deposit round-trips around the integration boundary for asymmetric conversions.
- Invariant to test: Kamino withdraw must release only value actually redeemed from the exact obligation/reserve pair and correct recipient
- Expected Immunefi impact: Critical: direct theft or phantom redemption
- Fast validation: Supply valid-looking optional accounts from another context and assert withdraw never succeeds against them. Run deposit-then-withdraw and withdraw-then-deposit loops and assert no positive attacker value emerges.
