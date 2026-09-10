# Baseline loading repeats: YCSB A-F at 50 GiB cached cache

Each freshly loaded baseline DB ran the full YCSB A-F set for 300 seconds per workload after a 3-second pilot, with 48 threads, Zipfian requests, automatic compaction enabled, cached metadata and a 50 GiB block cache. Every argument except the block-cache size and the DB/report paths is identical to the frozen cached-zero reference run, so results are comparable across these repeats but not against that cached-zero campaign.

Every cell used a fresh hardlink clone; original DB identities were verified unchanged and only validated disposable clones were removed. No manuscript change or commit was performed.
