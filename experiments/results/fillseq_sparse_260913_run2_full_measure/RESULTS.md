# Sparse sorted loading comparison

All cells use fresh hardlink clones of immutable source DBs. Request keyspace is held fixed; distinct count is not used as the query domain. The default sparse A/C protocol matches the historical baseline and flush-only commands apart from output paths. See reference_option_audit.json and manifest.json. Supplemental measurements, when explicitly enabled, are recorded separately in supplemental_results.json.
