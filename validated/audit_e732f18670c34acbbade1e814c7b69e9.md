### Title
Direct-IO alignment rounding in `ReadOp::entry`/`ReadOp::complete` allows reads past `read_limit` to leak into `fill_buf`/`read_exact` output - (File: fs/src/io_uring/sequential_file_reader.rs)

### Summary
`ReadOp::entry` rounds the requested O_DIRECT read length up to a 4096-byte boundary via `read_len.next_multiple_of(DIRECT_IO_READ_LEN_ALIGNMENT).min(buf.len() - buf_offset)`, so the kernel can be asked to read more bytes than the caller's logical `read_len`. `ReadOp::complete` only guards against *short* reads (`last_read_len < *read_len`) but not against the kernel actually returning the full, rounded-up `internal_read_len`, in which case `eof_pos` is set to `total_read_len` (the raw byte count), which can exceed the file's intended `read_limit` boundary for that read. Those extra bytes are then exposed as valid data through `fill_buf`/`read_exact`.

### Finding Description
In `FileState::next_read_op` (fs/src/io_uring/sequential_file_reader.rs:690-726), the last read of a file is sized to `read_len = left_to_read` (i.e., `read_limit - offset`), which is not required to be a multiple of `DIRECT_IO_READ_LEN_ALIGNMENT`, and is marked `is_last_read: left_to_read == read_len` (true).

In `ReadOp::entry` (lines 813-852):
```
let internal_read_len = if *is_direct_io && *read_len != buf.len() {
    read_len.next_multiple_of(DIRECT_IO_READ_LEN_ALIGNMENT).min(buf.len() - *buf_offset)
} else {
    *read_len
};
```
this rounds `read_len` up to the next 4096 boundary and submits `internal_read_len` (which is `> read_len` whenever `read_len` is unaligned) as the actual O_DIRECT read size, since the destination `IoBufferChunk` has room (`buf.len()` is itself a multiple of 4096 per the builder's assertion at lines 148-154).

In `ReadOp::complete` (lines 854-901):
```
let last_read_len = res? as IoSize;
let total_read_len = *buf_offset + last_read_len;
...
if last_read_len > 0 && last_read_len < *read_len {
    // Partial read, retry
} else {
    buffers[*reader_buf_index] = ReadBufState::Full {
        buf,
        eof_pos: (last_read_len == 0 || *is_last_read).then_some(total_read_len),
    };
}
```
The completion logic compares `last_read_len` against the *original, unaligned* `read_len`, not against `internal_read_len`. If the underlying file is physically longer than `read_limit` (e.g. a preallocated/pre-sized backing file), the kernel can fully satisfy the larger, rounded-up request and return `last_read_len == internal_read_len > read_len`. Because `last_read_len` is not `< read_len`, the "partial read" branch is skipped, and since `is_last_read` is `true`, `eof_pos` is set to `total_read_len`, which now includes the extra bytes beyond the caller's `read_limit`.

Downstream, `wait_current_buf_full` (lines 386-389) sets `current_buf_remaining = eof_pos.unwrap_or(buf.len())`, and `fill_buf`/`read_exact` (lines 436-475) hand out exactly `current_buf_remaining` bytes from the buffer — including the over-read tail — with no clamping back to the file's `read_limit`. The `FileState.next_read_offset` bookkeeping (`*offset += read_len`, line 723) correctly stops scheduling further reads for the file, but does nothing to trim the already-over-filled buffer's visible length.

### Impact Explanation
This is a data-integrity bug in the shared reader used by `accounts-db/src/account_storage_reader.rs` (`storage_file_buf_reader`, used with `use_direct_io` honored) which backs `snapshots/src/archive.rs`'s per-account-storage streaming during snapshot archive creation. An over-read causes bytes physically beyond an account storage file's logical `read_limit` (`storage.accounts.len()`) to be treated as valid stored-account bytes, i.e. wrong data attributed to the storage/account boundary — matching the "wrong account data returned" category. The scoped impact is confined to this reader; no crash or consensus mutation is involved.

### Likelihood Explanation
Requires `use_direct_io=true`, a `read_limit` not aligned to 4096 bytes (trivially true for `AppendVec::len()`, which is only aligned to 8 bytes via `u64_align!`), and a backing file physically longer than `read_limit` (true for `AppendVec` files, which are preallocated to `file_size` capacity via `data.set_len(size)` while `current_len` is generally smaller). These conditions are easily satisfied by normal, attacker-influenced account data sizes without any privileged access, since the alignment of `current_len` only depends on the total bytes of accounts written by any client's transactions.

### Recommendation
Clamp the exposed length to the original `read_len`/`read_limit` rather than the raw `last_read_len`/`total_read_len`. Concretely, in `ReadOp::complete`, compare `last_read_len` against `internal_read_len`-derived boundaries only for retry logic, but compute `eof_pos` using `min(total_read_len, buf_offset + read_len)` (or otherwise track the "logical" requested length separately from the "physical" aligned length actually submitted), so bytes read beyond the caller-specified `read_len`/`read_limit` are never surfaced via `fill_buf`/`read_exact`.

### Proof of Concept
Rust unit test to add to `fs/src/io_uring/sequential_file_reader.rs`'s `tests` module:
```rust
#[test]
fn test_direct_io_read_does_not_overrun_read_limit() {
    // read_limit intentionally NOT a multiple of DIRECT_IO_READ_LEN_ALIGNMENT (4096)
    let read_limit: u64 = 4000;
    let sentinel = vec![0xEE; 4096]; // bytes beyond read_limit that must never be visible

    let mut temp_file = tempfile::NamedTempFile::new().unwrap();
    let pattern: Vec<u8> = (0..read_limit as usize).map(|i| i as u8).collect();
    io::Write::write_all(&mut temp_file, &pattern).unwrap();
    io::Write::write_all(&mut temp_file, &sentinel).unwrap(); // physically longer than read_limit

    let buf = PageAlignedMemory::new(8192).unwrap();
    let mut reader = SequentialFileReaderBuilder::new()
        .use_direct_io(true)
        .read_capacity(4096)
        .build_with_buffer(buf)
        .unwrap();

    let file = std::fs::OpenOptions::new()
        .read(true)
        .custom_flags(libc::O_DIRECT)
        .open(temp_file.path())
        .unwrap();
    reader.add_owned_file_to_prefetch(file, read_limit).unwrap();

    let data = read_as_vec(&mut reader);
    assert_eq!(data.len() as u64, read_limit, "reader must not return bytes beyond read_limit");
    assert!(!data.ends_with(&sentinel[..4]), "sentinel bytes beyond read_limit leaked into output");
}
```
Expected (buggy) behavior: `data.len()` exceeds `read_limit` and includes sentinel bytes, failing the assertions. Expected after fix: `data.len() == read_limit` and no sentinel bytes present. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5) [7](#0-6)

### Citations

**File:** fs/src/io_uring/sequential_file_reader.rs (L386-410)
```rust
                ReadBufState::Full { buf, eof_pos } => {
                    if state.current_buf_remaining == 0 && state.current_buf_pos == 0 {
                        // Initialize consuming new buffer.
                        state.current_buf_remaining = eof_pos.unwrap_or(buf.len());
                        if state.left_to_consume > 0 {
                            // Skip any bytes remaining from previous unfulfilled consumes.
                            let consumed = state
                                .left_to_consume
                                .min(state.current_buf_remaining as usize);
                            state.left_to_consume -= consumed;
                            state.current_buf_pos += consumed as IoSize;
                            state.current_buf_remaining -= consumed as IoSize;
                        }
                    }

                    // Note: we might have consumed whole buf from `left_to_consume`
                    if state.current_buf_remaining > 0 {
                        // We have some data available.
                        return Ok(true);
                    }

                    if eof_pos.is_some() {
                        // Last filled buf for the whole file (until `move_to_next_file` is called).
                        return Ok(false);
                    }
```

**File:** fs/src/io_uring/sequential_file_reader.rs (L690-726)
```rust
    fn next_read_op(&mut self, index: u16, bufs: &mut [ReadBufState]) -> Option<ReadOp> {
        let Self {
            start_buf_index,
            raw_fd,
            is_direct_io,
            next_read_offset: offset,
            read_limit,
        } = self;
        let left_to_read = read_limit.saturating_sub(*offset);
        if left_to_read == 0 {
            return None;
        }

        let buf = bufs[index as usize].transition_to_reading();

        let read_len = left_to_read.min(buf.len() as FileSize);
        let op = ReadOp {
            fd: types::Fd(*raw_fd),
            buf,
            is_direct_io: *is_direct_io,
            buf_offset: 0,
            file_offset: *offset,
            read_len: read_len as u32, // it's trimmed by u32 buf.len() above
            is_last_read: left_to_read == read_len,
            reader_buf_index: index,
        };
        // Mark file state to start reading at `index` buffer
        if start_buf_index.is_none() {
            *start_buf_index = Some(index);
        }

        // We always advance by `read_len`. If we get a short read, we submit a new
        // read for the remaining data. See ReadOp::complete().
        *offset += read_len;

        Some(op)
    }
```

**File:** fs/src/io_uring/sequential_file_reader.rs (L812-852)
```rust
impl RingOp<BuffersState> for ReadOp {
    fn entry(&mut self) -> squeue::Entry {
        let ReadOp {
            fd,
            buf,
            is_direct_io,
            buf_offset,
            file_offset,
            read_len,
            is_last_read: _,
            reader_buf_index: _,
        } = self;

        // Align the read length if necessary
        let internal_read_len = if *is_direct_io && *read_len != buf.len() {
            // Try to align the read len if possible and fall back to reading
            // the full remaining bytes if we can't align the read len.
            read_len
                .next_multiple_of(DIRECT_IO_READ_LEN_ALIGNMENT)
                .min(buf.len() - *buf_offset)
        } else {
            *read_len
        };
        debug_assert!(*buf_offset + internal_read_len <= buf.len());
        // Safety: we assert that the buffer is large enough to hold the read.
        let buf_ptr = unsafe { buf.as_mut_ptr().byte_add(*buf_offset as usize) };

        let entry = match buf.io_buf_index() {
            Some(io_buf_index) => {
                opcode::ReadFixed::new(*fd, buf_ptr, internal_read_len, io_buf_index)
                    .offset(*file_offset)
                    .ioprio(IO_PRIO_BE_HIGHEST)
                    .build()
            }
            None => opcode::Read::new(*fd, buf_ptr, internal_read_len)
                .offset(*file_offset)
                .ioprio(IO_PRIO_BE_HIGHEST)
                .build(),
        };
        entry.flags(squeue::Flags::ASYNC)
    }
```

**File:** fs/src/io_uring/sequential_file_reader.rs (L854-901)
```rust
    fn complete(
        &mut self,
        completion: &mut Completion<BuffersState, Self>,
        res: io::Result<i32>,
    ) -> io::Result<()> {
        let ReadOp {
            fd,
            buf,
            is_direct_io,
            buf_offset,
            file_offset,
            read_len,
            is_last_read,
            reader_buf_index,
        } = self;
        let buffers = completion.context_mut();

        let last_read_len = res? as IoSize;

        let total_read_len = *buf_offset + last_read_len;
        let buf = mem::replace(buf, IoBufferChunk::empty());

        if last_read_len > 0 && last_read_len < *read_len {
            // Partial read, retry the op with updated offsets
            let op: ReadOp = ReadOp {
                fd: *fd,
                buf,
                is_direct_io: *is_direct_io,
                buf_offset: total_read_len,
                file_offset: *file_offset + last_read_len as FileSize,
                read_len: *read_len - last_read_len,
                reader_buf_index: *reader_buf_index,
                is_last_read: *is_last_read,
            };
            // Safety:
            // The op points to a buffer which is guaranteed to be valid for the
            // lifetime of the operation
            completion.push(op)?;
        } else {
            buffers[*reader_buf_index as usize] = ReadBufState::Full {
                buf,
                eof_pos: (last_read_len == 0 || *is_last_read).then_some(total_read_len),
            };
        }

        Ok(())
    }
}
```

**File:** accounts-db/src/account_storage_reader.rs (L34-52)
```rust
pub fn storage_file_buf_reader<'a>(
    max_buf_size: usize,
    use_page_cache: bool,
    io_setup: &IoSetupState,
) -> io::Result<StorageFileBufReader<'a>> {
    #[cfg(target_os = "linux")]
    {
        buffered_reader::SequentialFileReaderBuilder::new()
            .shared_sqpoll(io_setup.shared_sqpoll_fd())
            .use_direct_io(io_setup.use_direct_io && !use_page_cache)
            .use_registered_buffers(io_setup.use_registered_io_uring_buffers)
            .build(max_buf_size)
    }
    #[cfg(not(target_os = "linux"))]
    {
        let _ = (max_buf_size, use_page_cache, io_setup);
        Ok(StorageFileBufReader::new())
    }
}
```

**File:** accounts-db/src/append_vec.rs (L209-249)
```rust
    pub fn new(file: impl Into<PathBuf>, size: usize) -> Self {
        let file = file.into();
        let initial_len = 0;
        AppendVec::sanitize_len_and_size(initial_len, size).unwrap();

        let _ignored = remove_file(&file);

        let data = OpenOptions::new()
            .read(true)
            .write(true)
            .create_new(true)
            .open(&file)
            .map_err(|e| {
                panic!(
                    "Unable to create data file {} in current dir({:?}): {:?}",
                    file.display(),
                    std::env::current_dir(),
                    e
                );
            })
            .unwrap();

        // Theoretical performance optimization: set the logical/inode size
        // so that we don't have to resize it later, which may be expensive.
        let size = u64::try_from(size).unwrap();
        data.set_len(size).unwrap();

        APPEND_VEC_STATS.files_open.fetch_add(1, Ordering::Relaxed);

        AppendVec {
            path: file,
            file: data,
            // writable state's mutex forces append to be single threaded, but concurrent with
            // reads. See UNSAFE usage in `append_ptr`
            read_write_state: ReadWriteState::new(true),
            current_len: AtomicUsize::new(initial_len),
            file_size: size,
            remove_file_on_drop: AtomicBool::new(true),
            is_dirty: AtomicBool::new(false),
        }
    }
```

**File:** accounts-db/src/append_vec.rs (L308-320)
```rust
    /// Returns the number of bytes, *not items*, used in the AppendVec
    pub fn len(&self) -> usize {
        self.current_len.load(Ordering::Acquire)
    }

    pub fn is_empty(&self) -> bool {
        self.len() == 0
    }

    /// Returns the total number of bytes, *not items*, the AppendVec can hold
    pub fn capacity(&self) -> u64 {
        self.file_size
    }
```
