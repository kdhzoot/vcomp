// PLR error bound sensitivity probe: does a larger delta degrade key reconstruction?
#include <cstdio>
#include <cstdint>
#include <cmath>
#include <algorithm>
#include <vector>
#include <string>
#include "db/virtual_compaction/plr_model.h"
using namespace ROCKSDB_NAMESPACE;

struct SM64 { uint64_t s; uint64_t next(){ uint64_t z=(s+=0x9e3779b97f4a7c15ULL);
  z=(z^(z>>30))*0xbf58476d1ce4e5b9ULL; z=(z^(z>>27))*0x94d049bb133111ebULL; return z^(z>>31);} };

static std::vector<uint64_t> gen(const std::string& kind, size_t n, uint64_t seed) {
  SM64 r{seed}; std::vector<uint64_t> v; v.reserve(n);
  if (kind=="uniform_dense") {            // 우리 실험이 쓴 분포: 도메인 = n 근처
    for(size_t i=0;i<n;i++) v.push_back(r.next() % (n*4));
  } else if (kind=="uniform_sparse") {    // 같은 균등분포지만 도메인이 2^63
    for(size_t i=0;i<n;i++) v.push_back(r.next() >> 1);
  } else if (kind=="sequential") {        // 완전 선형
    for(size_t i=0;i<n;i++) v.push_back(i*1000);
  } else if (kind=="clustered") {         // 90%가 도메인의 0.1%에 몰림
    for(size_t i=0;i<n;i++) v.push_back((r.next()%100<90) ? (r.next()%(n*4)) : (r.next()>>1));
  } else if (kind=="multimodal") {        // 8개 좁은 봉우리
    for(size_t i=0;i<n;i++){ uint64_t c=(r.next()%8)*(uint64_t{1}<<60); v.push_back(c + (r.next()%(n*4))); }
  } else if (kind=="heavytail") {         // 간격이 지수적으로 증가
    double x=1.0; for(size_t i=0;i<n;i++){ x*=1.0+ (double)(r.next()%1000)/50000.0;
      v.push_back((uint64_t)std::min(x*1e6,(double)(uint64_t{1}<<62))); }
  } else if (kind=="dup_heavy") {         // 중복이 매우 많음 (unique 5%)
    for(size_t i=0;i<n;i++) v.push_back((r.next()%(n/20+1))*7919);
  }
  std::sort(v.begin(), v.end());
  return v;
}

int main(int argc,char**argv){
  const size_t N = argc>1 ? (size_t)atoll(argv[1]) : 65536;
  const char* dists[] = {"uniform_dense","uniform_sparse","sequential","clustered","multimodal","heavytail","dup_heavy"};
  const double deltas[] = {8,16,32,64,128,256,1024,4096,65536};
  printf("n=%zu\n",N);
  printf("%-16s %8s %9s %12s %14s %14s %10s %9s\n",
         "dist","delta","segments","max_rank_err","med_key_err","max_key_err","dup_ranks","nonmono");
  for(auto d:dists){
    auto keys = gen(d,N,12345678);
    // 실제 distinct 키 수
    std::vector<uint64_t> uq=keys; uq.erase(std::unique(uq.begin(),uq.end()),uq.end());
    for(double eb:deltas){
      PLRModel m = GreedyPLRFit(keys, eb);
      double max_rank_err=0;
      for(size_t i=0;i<keys.size();i+= (keys.size()>20000?7:1))
        max_rank_err = std::max(max_rank_err, std::fabs(m.Predict(keys[i]) - (double)i));
      // 키 복원 오차: rank i 를 역변환한 키 vs 진짜 keys[i]
      std::vector<double> kerr; kerr.reserve(keys.size()/7+1);
      uint64_t prev=0; size_t dup=0, nonmono=0; bool first=true;
      double max_key_err=0;
      for(size_t i=0;i<keys.size();i++){
        uint64_t got = m.Inverse((double)i);
        if(!first){ if(got==prev) dup++; else if(got<prev) nonmono++; }
        prev=got; first=false;
        double e = std::fabs((double)got - (double)keys[i]);
        max_key_err=std::max(max_key_err,e);
        if(i%7==0) kerr.push_back(e);
      }
      std::sort(kerr.begin(),kerr.end());
      double med = kerr.empty()?0:kerr[kerr.size()/2];
      printf("%-16s %8.0f %9zu %12.1f %14.3e %14.3e %9.2f%% %9.2f%%\n",
             d,eb,m.NumSegments(),max_rank_err,med,max_key_err,
             100.0*dup/keys.size(), 100.0*nonmono/keys.size());
    }
    printf("\n");
  }
  return 0;
}
