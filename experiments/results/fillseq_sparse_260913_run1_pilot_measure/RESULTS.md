# Sparse sorted loading comparison

All cells use fresh hardlink clones of immutable source DBs. Request keyspace is held fixed; distinct count is not used as the query domain. Uniform reads and YCSB use the same frozen profiler binary. Fixed-operation counters include the compaction drain. See manifest.json and supplemental_results.json.
