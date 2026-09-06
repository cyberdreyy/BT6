[1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

### Citations

**File:** stacks-signer/src/v0/signer.rs (L1-1)
```rust
// Copyright (C) 2020-2026 Stacks Open Internet Foundation
```

**File:** docs/signer-flows.md (L242-243)
```markdown
    STORE --> ALREADY{"signed_self already set?"}
    ALREADY -- yes --> N1(["nothing to do"])
```

**File:** docs/signer-flows.md (L248-268)
```markdown
    TH -- yes --> RECHECK{"chainstate checks still pass?<br/>check_block_against_signer_db_state<br/>→ section 7"}
    RECHECK -- no --> REJ["mark_locally_rejected,<br/>handle_block_rejection,<br/>broadcast rejection"]:::bad
    RECHECK -- yes --> CONF["signed conflicts at height ≥ h,<br/>in ANY tenure<br/>get_signed_conflicts"]
    CONF --> PERM{"covered by a reorg permit whose<br/>permitting sortition is still canonical?<br/>reorg_permit_stands"}
    PERM -- yes --> EXCL(["excluded — our signature must not<br/>block a replacement we sanctioned"]):::good
    PERM -- no --> FRESH{"any of them still fresh?<br/>last_endorsed > cutoff"}
    FRESH -- yes --> SORT{"conflict_still_blocks, question 1:<br/>is its tenure's sortition still on the<br/>canonical burn chain?<br/>get_sortition_by_burn_hash"}
    SORT -- "404, with the node's burnchain tip<br/>at or past the burn block — a fork<br/>orphaned the tenure" --> OWN
    SORT -- "canonical, or we never<br/>saved its burn block" --> LIVE{"question 2: does the node's chain<br/>still reach the block itself?<br/>get_tenure_tip(its tenure)"}
    SORT -- "could not ask, or 404 with the<br/>node's tip still below the burn block" --> HOLD1
    LIVE -- "yes — real chain state" --> HOLD1["refuse to sign for now<br/>(may sign once conflict is stale)"]:::hold
    LIVE -- "no, and it was<br/>globally accepted" --> OWN
    LIVE -- "no, only locally accepted<br/>— but above this height" --> OWN
    LIVE -- "no, only locally accepted<br/>and a sibling at this height" --> HOLD1
    LIVE -- "could not ask" --> HOLD1
    FRESH -- "no — all stale" --> OWN{"a conflict in this block's<br/>OWN tenure?"}
    OWN -- yes --> TIP{"own tenure confirmed<br/>at ≥ this height?<br/>get_tenure_tip(own tenure)"}
    TIP -- yes --> HOLD2["refuse to sign"]:::hold
    TIP -- "no — never confirmed" --> SIGN
    TIP -- "node unreachable" --> SIGN
    OWN -- no --> SIGN["SIGN: mark_locally_accepted,<br/>handle_block_signature,<br/>broadcast acceptance"]:::good
```

**File:** docs/signer-flows.md (L288-297)
```markdown
Freshness alone is not enough to hold a signature back, because a signature can
outlive the block it covers: a Bitcoin reorg can kill the block, and a dead
signature must not stall the chain restarting beneath it until it goes stale. So
`conflict_still_blocks` derives, per evaluation, whether the conflict could still
end up in the chain. Deriving this here — instead of recording it when a fork is
observed — is deliberate: the node's view mid-reorg is a moving target (burn
block events fire before the sortition transaction commits, and a node error can
wipe the local state machine), so a fact recorded once at observation time can be
silently wrong, while a question asked per evaluation self-corrects on the next
pre-commit or re-proposal. Two questions, in order:
```

**File:** docs/signer-flows.md (L487-509)
```markdown
Burnchain forks need no bookkeeping here. Whether a fork orphaned a tenure whose
block we signed is derived at signing time from the burn block records this flow
saves on every arrival (section 5, question 1), so the burn block events
themselves are all the fork handling this flow owes: nothing has to be observed
at the right moment, survive an error that resets the local state machine, or be
restored when the burnchain forks _back_ onto a branch it had abandoned — in that
last case the sortition is simply canonical again, and the very next evaluation
sees the conflict as live.

One decision does have to be recorded, because it is ours rather than the
node's. When a miner builds off something other than the prior sortition,
`check_parent_tenure_choice` decides whether the reorg is allowed: it is, if
every tenure being reorged has at most one globally accepted block and produced
its first block too close to the next sortition to count
(`first_proposal_burn_block_timing`). Having sanctioned that replacement, the
signer records those tenures as **superseded** (`mark_tenure_superseded`), so its
own signature over what they built does not then block the replacement it just
permitted — the node cannot answer this one at signing time, since it still
serves the reorged tenure as fully live until the replacement lands. What _is_
still derived from the node is the permit's own validity: the record carries the
permitting tenure's sortition, and it only excludes conflicts while that
sortition remains canonical (section 5, `reorg_permit_stands`), so a burnchain
fork that orphans the permitting tenure automatically voids the permit. A record
```
