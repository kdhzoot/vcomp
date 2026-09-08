// Copyright (c) 2011-present, Facebook, Inc. All rights reserved.
// This source code is licensed under both the GPLv2 (found in the
// COPYING file in the root directory) and Apache 2.0 License
// (found in the LICENSE.Apache file in the root directory).
#pragma once

#include <cstdint>
#include <vector>

#include "rocksdb/status.h"

namespace ROCKSDB_NAMESPACE {

class PLRModel;
struct VirtualSST;

// Construct a count-feasible discrete model without enumerating original keys.
// All retained sample IDs and nonempty input extrema are mandatory witnesses.
// requested_total is projected into [witness count, envelope-union capacity].
// Reject a projected total above the sum of input descriptor counts. The output
// and accepted_total remain unchanged on failure; no legacy fallback is used.
Status BuildDiscreteMergeModel(const std::vector<const VirtualSST*>& inputs,
                               uint64_t requested_total, PLRModel* output,
                               uint64_t* accepted_total);

}  // namespace ROCKSDB_NAMESPACE
