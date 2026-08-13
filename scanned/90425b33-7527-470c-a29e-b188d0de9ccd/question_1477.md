# Q1477: kamino_withdraw: withdraw accepts attacker-shaped optional accounts at closeout [same-slot-deposit-then-withdraw] [recipient-binding]

## Question
Can an unprivileged attacker use `kamino_withdraw` with same-slot deposit then withdraw around the same external position so `kamino_withdraw` accepts attacker-shaped optional accounts during closeout, violating `Kamino withdraw must release only value actually redeemed from the exact obligation/reserve pair and correct recipient` and causing `Critical: direct theft or phantom redemption`? Focus specifically on destination binding across intermediary ATAs, vault authorities, and final transfer-out.

## Target
- File/function: `programs/marginfi/src/instructions/kamino/withdraw.rs` / `kamino_withdraw`
- Entrypoint: `kamino_withdraw`
- Attacker controls: same-slot deposit then withdraw around the same external position
- Exploit idea: Probe optional reward, mint, reserve, or destination accounts used only during withdraw and therefore easy to under-validate. Focus specifically on destination binding across intermediary ATAs, vault authorities, and final transfer-out.
- Invariant to test: Kamino withdraw must release only value actually redeemed from the exact obligation/reserve pair and correct recipient
- Expected Immunefi impact: Critical: direct theft or phantom redemption
- Fast validation: Supply valid-looking optional accounts from another context and assert withdraw never succeeds against them. Swap every plausible destination and assert no accepted path releases value to an unvalidated recipient.
