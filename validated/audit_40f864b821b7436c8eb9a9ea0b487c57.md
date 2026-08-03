[1](#0-0)

### Citations

**File:** aptos-move/block-executor/src/captured_reads.rs (L63-99)
```rust
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) enum ReadKind {
    Exists,
    Metadata,
    ResourceSize,
    MetadataAndResourceSize,
    Value,
}

/// The enum captures the state that the transaction execution extracted from
/// a read callback to block executor, in order to be validated by Block-STM.
/// The captured state is fine-grained, e.g. it distinguishes between reading
/// a full value, and other kinds of reads that may access only the metadata
/// information, or check whether data exists at a given key.
#[derive(Derivative)]
#[derivative(Clone(bound = ""), Debug(bound = ""), PartialEq(bound = ""))]
pub(crate) enum DataRead<V> {
    // Version supersedes V comparison.
    Versioned(
        Version,
        // Currently, we are conservative and check the version for equality
        // (version implies value equality, but not vice versa). TODO: when
        // comparing the instances of V is cheaper, compare those instead.
        #[derivative(PartialEq = "ignore", Debug = "ignore")] TriompheArc<V>,
        #[derivative(PartialEq = "ignore", Debug = "ignore")] Option<TriompheArc<MoveTypeLayout>>,
    ),
    // Metadata and ResourceSize are insufficient to determine each other, but both
    // can be determined from Versioned. When both are available, the information
    // is stored in the MetadataAndResourceSize variant.
    MetadataAndResourceSize(Option<StateValueMetadata>, Option<u64>),
    Metadata(Option<StateValueMetadata>),
    ResourceSize(Option<u64>),
    // Exists is a lower tier, can be determined both from Metadata and ResourceSize.
    Exists(bool),
    // CAUTION: when adding a new variant here, it must be ensured that compare
    // data reads implements a comparison (o.w. unreachable arm will be hit).
}
```
