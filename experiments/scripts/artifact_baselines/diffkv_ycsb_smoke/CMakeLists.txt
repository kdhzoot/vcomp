cmake_minimum_required(VERSION 3.10)
project(diffkv_ycsb_smoke LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 11)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
# The upstream YCSB-C calls DB::Insert from inside assert().  NDEBUG would
# remove the write itself, so keep assertions enabled even if a caller selects
# a release-like CMake build type.
set(CMAKE_CXX_FLAGS "${CMAKE_CXX_FLAGS} -O2 -g -Wall -pthread -UNDEBUG")

if(NOT DEFINED DIFFKV_ROOT)
  message(FATAL_ERROR "DIFFKV_ROOT is required")
endif()
if(NOT DEFINED BOOST_INCLUDE_DIR)
  message(FATAL_ERROR "BOOST_INCLUDE_DIR is required")
endif()

set(YCSB_ROOT "${DIFFKV_ROOT}/bench_tools/YCSB-C")
set(ROCKSDB_ROOT "${DIFFKV_ROOT}/dep/rocksdb")

add_executable(ycsbc_smoke
  "${YCSB_ROOT}/ycsbc.cc"
  "${YCSB_ROOT}/core/core_workload.cc"
  "${YCSB_ROOT}/core/histogram.cc"
  "${CMAKE_CURRENT_SOURCE_DIR}/titandb_db_smoke.cc"
  "${CMAKE_CURRENT_SOURCE_DIR}/db_factory_smoke.cc"
)

add_executable(verify_diffkv_smoke
  "${CMAKE_CURRENT_SOURCE_DIR}/verify_diffkv_smoke.cc"
)

target_include_directories(ycsbc_smoke PRIVATE
  "${YCSB_ROOT}"
  "${YCSB_ROOT}/core"
  "${YCSB_ROOT}/db"
  "${DIFFKV_ROOT}/include"
  "${ROCKSDB_ROOT}/include"
  "${ROCKSDB_ROOT}"
  "${BOOST_INCLUDE_DIR}"
)
target_compile_options(ycsbc_smoke PRIVATE -UNDEBUG)

target_include_directories(verify_diffkv_smoke PRIVATE
  "${YCSB_ROOT}"
  "${YCSB_ROOT}/core"
  "${YCSB_ROOT}/db"
  "${DIFFKV_ROOT}/include"
  "${ROCKSDB_ROOT}/include"
  "${ROCKSDB_ROOT}"
  "${BOOST_INCLUDE_DIR}"
)
target_compile_options(verify_diffkv_smoke PRIVATE -UNDEBUG)

target_link_libraries(ycsbc_smoke PRIVATE
  "${DIFFKV_ROOT}/build/libdiffkv.a"
  "${ROCKSDB_ROOT}/librocksdb.a"
  snappy
  tbb
  z
  dl
  pthread
)

target_link_libraries(verify_diffkv_smoke PRIVATE
  "${DIFFKV_ROOT}/build/libdiffkv.a"
  "${ROCKSDB_ROOT}/librocksdb.a"
  snappy
  tbb
  z
  dl
  pthread
)
