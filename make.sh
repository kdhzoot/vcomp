#!/bin/bash

make clean

export CC=gcc-11
export CXX=g++-11

make static_lib db_bench -j $(nproc)