# Chapter 3.1 uniform YCSB C

Three completed 300-second C measurements on fresh retained hard-link clones. Original key domain, 48 threads, 50 GiB cache, direct I/O, seed and engine options retained; only request distribution changed to uniform. Global page cache reset succeeded before each process. Filter checks=(bloom.filter.useful+bloom.filter.full.positive)/number.keys.read. Positive lookups use all Get requests as denominator. C has no explicit compaction drain: its counters describe the 300-second read window; flush-only retains pending compaction as expected.

The user cancelled further A measurements after C completed. Baseline/fillseq A had already completed and remain in the raw campaign bundle; flush-only A was stopped and is not a completed measurement. All source identities were rechecked and unchanged. The copied manifest describes the original planned A/C campaign; only the three C rows are promoted here. One repetition per system. No paper text or figures updated.
