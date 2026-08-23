# Q585: vm_slice::ptr — signer flag escalation

## Question
Can an unprivileged attacker, through a deployed SBPF program issuing a CPI, reach `vm_slice::ptr` and construct a CPI whose child instruction carries a signer privilege the caller never held, via the AccountMeta/is_signer plumbing, so that the invariant "a CPI never grants a signer flag the caller did not itself hold" is violated, leading to Loss of Funds (sign for victim)?

## Target
- File/function: `transaction-context/src/vm_slice.rs` -> `ptr`
- Entrypoint: a deployed SBPF program issuing a CPI
- Attacker controls: the account metas and signers_seeds passed to invoke_signed from its program
- Exploit idea: Construct a CPI whose child instruction carries a signer privilege the caller never held, via the AccountMeta/is_signer plumbing.
- Invariant to test: a CPI never grants a signer flag the caller did not itself hold.
- Expected Immunefi impact: Loss of Funds (sign for victim) — Critical
- Fast validation: write a program-test invoking invoke_signed with the crafted metas/seeds and assert the privilege is not escalated.
