// Offline replay of captured Put-only compactions. No RocksDB DB is opened.
// VCOMP_REPLAY_ARCHIVED_LEGACY builds against the archived pre-discrete sources.
#include <algorithm>
#include <chrono>
#include <cmath>
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <ctime>
#include <filesystem>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <limits>
#include <map>
#include <memory>
#include <queue>
#include <set>
#include <sstream>
#include <stdexcept>
#include <string>
#include <vector>
#include <fcntl.h>
#include <sys/mman.h>
#include <sys/stat.h>
#include <unistd.h>

#include "db/virtual_compaction/virtual_sst.h"

namespace {
namespace r = ROCKSDB_NAMESPACE;
namespace fs = std::filesystem;
using Wide = unsigned __int128;
using Keys = std::vector<uint64_t>;
constexpr uint64_t kMax = std::numeric_limits<uint64_t>::max();
void Need(bool ok, const std::string& why) { if (!ok) throw std::runtime_error(why); }
uint64_t Unsigned(const std::string& value, const std::string& name) {
  Need(!value.empty(), "empty integer: " + name);
  uint64_t n = 0;
  for (char c : value) {
    Need(c >= '0' && c <= '9' && n <= (kMax - (c-'0')) / 10, "invalid integer: " + name);
    n = n*10 + c-'0';
  }
  return n;
}
std::string Quote(const std::string& value) {
  std::ostringstream out; out << '"';
  for (unsigned char c : value) {
    if (c == '\\' || c == '"') out << '\\' << c;
    else if (c < 32) out << "\\u" << std::hex << std::setw(4) << std::setfill('0') << unsigned(c) << std::dec;
    else out << c;
  }
  out << '"'; return out.str();
}
std::string Decimal(Wide n) {
  if (!n) return "0";
  std::string out; while (n) { out.push_back('0' + n%10); n /= 10; }
  std::reverse(out.begin(), out.end()); return out;
}
struct Json {
  std::map<std::string, std::string> fields;
  void Text(const std::string& name, const std::string& value) { fields[name] = Quote(value); }
  template<class T> void Number(const std::string& name, T value) {
    std::ostringstream out; out << std::setprecision(18) << value; fields[name] = out.str();
  }
  void Bool(const std::string& name, bool value) { fields[name] = value ? "true" : "false"; }
  std::string Dump() const {
    std::string out = "{"; bool comma = false;
    for (const auto& item : fields) { if (comma) out += ','; comma = true; out += Quote(item.first) + ':' + item.second; }
    return out + '}';
  }
};
std::string Array(const std::vector<Json>& rows) {
  std::string out = "["; for (size_t i=0;i<rows.size();++i) { if (i) out += ','; out += rows[i].Dump(); } return out + ']';
}
struct Options {
  fs::path capture, output, work;
  std::string variant = "discrete_certified";
  uint64_t samples = 512, buckets = 8, max_buffer_keys = 50000000;
  double error = 8;
  bool force_stream_merged = false;
};
Options Parse(int argc, char** argv) {
  Options o;
  for (int i=1;i<argc;++i) {
    std::string name=argv[i], value;
    if (name == "--stream-merged") { o.force_stream_merged = true; continue; }
    const auto equal = name.find('=');
    if (equal != std::string::npos) { value=name.substr(equal+1); name.resize(equal); }
    else { Need(i+1<argc, "missing argument: " + name); value=argv[++i]; }
    if (name=="--capture" || name=="--manifest") o.capture=value;
    else if (name=="--output") o.output=value;
    else if (name=="--work-dir") o.work=value;
    else if (name=="--variant" || name=="--mode") o.variant=value;
    else if (name=="--samples" || name=="--kmv-samples") o.samples=Unsigned(value,name);
    else if (name=="--buckets" || name=="--range-buckets") o.buckets=Unsigned(value,name);
    else if (name=="--max-buffer-keys") o.max_buffer_keys=Unsigned(value,name);
    else if (name=="--plr-error") {
      size_t used=0; o.error=std::stod(value,&used);
      Need(used==value.size() && std::isfinite(o.error) && o.error>=0 && o.error<=1000000000, "invalid PLR error");
    } else throw std::runtime_error("unknown option: " + name);
  }
  Need(!o.capture.empty() && !o.output.empty(), "--capture and --output are required");
  Need(o.samples>0 && o.samples<=1048576 && o.buckets>0 && o.buckets<=4096 &&
       o.max_buffer_keys>0 && o.max_buffer_keys<=1000000000, "budget outside supported bounds");
  Need(o.variant=="legacy" || o.variant=="discrete_raw" || o.variant=="discrete_certified", "invalid variant");
  if (o.work.empty()) o.work=fs::path("/work/vcomp/exp/offline_replay_tmp");
  return o;
}

struct Mapped {
  fs::path path; const unsigned char* data=nullptr; uint64_t count=0; size_t bytes=0;
  explicit Mapped(const fs::path& name) : path(name) {
    const int fd=open(name.c_str(),O_RDONLY|O_CLOEXEC); Need(fd>=0,"cannot open keys: "+name.string());
    struct stat st{}; const bool valid=fstat(fd,&st)==0 && S_ISREG(st.st_mode) && st.st_size>=0 && st.st_size%8==0;
    if (!valid) { close(fd); throw std::runtime_error("invalid LEu64 file: "+name.string()); }
    bytes=static_cast<size_t>(st.st_size); count=bytes/8;
    void* ptr=bytes?mmap(nullptr,bytes,PROT_READ,MAP_PRIVATE,fd,0):nullptr;
    close(fd); Need(ptr!=MAP_FAILED,"cannot mmap keys: "+name.string()); data=static_cast<const unsigned char*>(ptr);
  }
  ~Mapped() { if (data) munmap(const_cast<unsigned char*>(data),bytes); }
  uint64_t At(uint64_t index) const {
    uint64_t result=0; const auto* p=data+index*8;
#if defined(__BYTE_ORDER__) && __BYTE_ORDER__ == __ORDER_LITTLE_ENDIAN__
    std::memcpy(&result,p,sizeof(result)); return result;
#else
    for (unsigned b=0;b<8;++b) result|=uint64_t{p[b]}<<(8*b);
    return result;
#endif
  }
};
using Runs=std::vector<std::shared_ptr<Mapped>>;
struct File {
  uint64_t number=0,level=0,physical_bytes=0,physical_entries=0,count=0,minimum=0,maximum=0;
  std::shared_ptr<Mapped> keys;
};
struct Capture {
  std::map<std::string,std::string> scalars;
  std::vector<File> inputs,outputs;
  std::vector<uint64_t> gp;
  uint64_t key_size=0,value_size=0,target=0,output_level=0,input_sum=0;
};
std::vector<std::string> Tabs(const std::string& line) {
  std::vector<std::string> parts; size_t begin=0;
  for (;;) { size_t end=line.find('\t',begin); parts.push_back(line.substr(begin,end-begin)); if (end==std::string::npos) return parts; begin=end+1; }
}
Capture ReadCapture(const fs::path& manifest) {
  std::ifstream input(manifest); Need(bool(input),"cannot open capture manifest");
  const auto base=fs::canonical(manifest).parent_path(); Capture c; std::string line; bool schema=false,finished=false;
  std::set<std::string> files;
  while (std::getline(input,line)) {
    if (!line.empty() && line.back()=='\r') line.pop_back();
    if (line.empty()) continue;
    Need(!finished,"records after final status"); const auto fields=Tabs(line);
    if (!schema) { Need(fields==std::vector<std::string>({"schema","vcomp_real_input_v1"}),"invalid capture schema"); schema=true; continue; }
    if (fields[0]=="status") { Need(fields.size()==2 && fields[1]=="ok","capture is not complete/ok"); finished=true; }
    else if (fields[0]=="input" || fields[0]=="output") {
      Need(fields.size()==9,"invalid file record"); File f;
      f.number=Unsigned(fields[1],"file number"); f.level=Unsigned(fields[2],"file level");
      f.physical_bytes=Unsigned(fields[3],"physical bytes"); f.physical_entries=Unsigned(fields[4],"physical entries");
      f.count=Unsigned(fields[5],"unique entries"); f.minimum=Unsigned(fields[6],"minimum"); f.maximum=Unsigned(fields[7],"maximum");
      Need(f.count>0 && f.physical_entries>=f.count && f.minimum<=f.maximum && f.level<=63,"invalid file metadata");
      fs::path relative(fields[8]); Need(!relative.empty() && !relative.is_absolute(),"raw filename must be relative");
      for (const auto& component:relative) Need(component!="..","parent traversal in raw filename");
      const auto path=fs::canonical(base/relative); auto bi=base.begin(),pi=path.begin();
      for (;bi!=base.end();++bi,++pi) Need(pi!=path.end() && *bi==*pi,"raw file escapes capture directory");
      Need(files.insert(path.string()).second,"duplicate raw file path");
      f.keys=std::make_shared<Mapped>(path); Need(f.keys->count==f.count,"raw count mismatch");
      Need(f.keys->At(0)==f.minimum && f.keys->At(f.count-1)==f.maximum,"raw extrema mismatch");
      for (uint64_t i=1;i<f.count;++i) Need(f.keys->At(i-1)<f.keys->At(i),"raw keys are not strictly increasing");
      if (fields[0]=="input") { Need(c.input_sum<=kMax-f.count,"input sum overflow"); c.input_sum+=f.count; c.inputs.push_back(std::move(f)); }
      else c.outputs.push_back(std::move(f));
    } else if (fields[0]=="gp") {
      Need(fields.size()==3,"invalid GP record"); const auto lo=Unsigned(fields[1],"GP min"),hi=Unsigned(fields[2],"GP max");
      Need(lo<=hi,"invalid GP range"); c.gp.push_back(lo); c.gp.push_back(hi);
    } else {
      Need(fields.size()==2 && c.scalars.emplace(fields[0],fields[1]).second,"invalid/duplicate scalar");
    }
  }
  Need(input.eof() && schema && finished && !c.inputs.empty() && !c.outputs.empty(),"incomplete capture");
  for (const std::string name:{"job","cf_id","start_level","output_level","target_sst_size","key_size","value_size","plr_error"})
    Need(c.scalars.count(name)!=0,"missing scalar: "+name);
  c.key_size=Unsigned(c.scalars.at("key_size"),"key size"); c.value_size=Unsigned(c.scalars.at("value_size"),"value size");
  c.target=Unsigned(c.scalars.at("target_sst_size"),"target size"); c.output_level=Unsigned(c.scalars.at("output_level"),"output level");
  Need(c.key_size>=8 && c.key_size<=kMax-c.value_size && c.key_size+c.value_size>0 && c.target>0 && c.output_level<=63,"invalid job sizes/level");
  Need(Unsigned(c.scalars.at("cf_id"),"column family")==0,"only default column family captures are supported");
  Need(Unsigned(c.scalars.at("start_level"),"start level")<=63,"invalid start level");
  for(const auto& f:c.outputs)Need(f.level==c.output_level,"output file level differs from job output level");
  if(c.scalars.count("input_files"))Need(Unsigned(c.scalars.at("input_files"),"input_files")==c.inputs.size(),"input file count scalar mismatch");
  if(c.scalars.count("output_files"))Need(Unsigned(c.scalars.at("output_files"),"output_files")==c.outputs.size(),"output file count scalar mismatch");
  if(c.scalars.count("binary_encoding"))Need(c.scalars.at("binary_encoding")=="uint64_le","unsupported binary encoding");
  std::sort(c.gp.begin(),c.gp.end()); return c;
}
Runs FileRuns(const std::vector<File>& files) { Runs result; for (const auto& f:files) result.push_back(f.keys); return result; }
class UnionCursor {
  struct Entry { uint64_t key,index,position; bool operator>(const Entry& b) const { return key>b.key || (key==b.key && index>b.index); } };
  const Runs& runs_; std::priority_queue<Entry,std::vector<Entry>,std::greater<Entry>> heap_;
 public:
  explicit UnionCursor(const Runs& runs):runs_(runs) { for(size_t i=0;i<runs.size();++i) if(runs[i]->count) heap_.push({runs[i]->At(0),i,0}); }
  bool Next(uint64_t* key) {
    if(heap_.empty())return false; *key=heap_.top().key;
    do { auto e=heap_.top(); heap_.pop(); if(++e.position<runs_[e.index]->count) { e.key=runs_[e.index]->At(e.position); heap_.push(e); } }
    while(!heap_.empty() && heap_.top().key==*key);
    return true;
  }
};
uint64_t CountUnion(const Runs& runs) { UnionCursor cursor(runs); uint64_t key,count=0; while(cursor.Next(&key)) { Need(count<kMax,"union count overflow"); ++count; } return count; }
uint64_t VerifySameUnion(const Runs& a,const Runs& b) {
  UnionCursor left(a),right(b); uint64_t x=0,y=0,count=0;
  for (;;) { const bool ax=left.Next(&x),by=right.Next(&y); Need(ax==by && (!ax || x==y),"input/output exact union mismatch (unsupported snapshot/deletion or invalid capture)"); if(!ax)return count; ++count; }
}
struct Temp {
  fs::path path; uint64_t serial=0;
  explicit Temp(const fs::path& root) {
    fs::create_directories(root); path=root/("replay_"+std::to_string(getpid()));
    Need(fs::create_directory(path),"temporary replay directory already exists");
  }
  ~Temp() { std::error_code ec; fs::remove_all(path,ec); }
  fs::path Next() { return path/(std::to_string(serial++)+".u64"); }
};
void WriteKey(std::ofstream& out,uint64_t key) { char bytes[8]; for(unsigned i=0;i<8;++i)bytes[i]=char(key>>(8*i)); out.write(bytes,8); }
struct Generated { Runs runs; uint64_t planned=0,raw_rows=0,perfile_distinct=0; bool valid=true; std::string source="MaterializeKeys"; };
std::shared_ptr<Mapped> WriteVector(Keys* keys,Temp* temp) {
  std::sort(keys->begin(),keys->end()); keys->erase(std::unique(keys->begin(),keys->end()),keys->end());
  const auto path=temp->Next(); std::ofstream out(path,std::ios::binary); Need(bool(out),"cannot create generated run");
  for(auto key:*keys)WriteKey(out,key); out.close(); Need(bool(out),"generated run write failed");
  return std::make_shared<Mapped>(path);
}
Generated Generate(const std::vector<r::VirtualSST>& files,const Options& o,Temp* temp) {
  Generated g;
  for(const auto& file:files) {
    Need(file.num_entries<=o.max_buffer_keys,"input/output SST exceeds --max-buffer-keys");
    Need(g.planned<=kMax-file.num_entries,"descriptor sum overflow"); g.planned+=file.num_entries;
    if(file.key_min>file.key_max || (file.num_entries && file.plr_model.Empty())) { g.valid=false;continue; }
    auto keys=r::MaterializeKeys(file); g.raw_rows+=keys.size(); auto run=WriteVector(&keys,temp);
    g.perfile_distinct+=run->count; g.runs.push_back(std::move(run));
  }
  return g;
}
Generated GenerateMerged(const r::VirtualSST& file,const Options& o,Temp* temp) {
  if(file.num_entries==0)return Generated{};
  if(file.key_min>file.key_max || file.plr_model.Empty()) {Generated g;g.planned=file.num_entries;g.valid=false;return g;}
  if(!o.force_stream_merged && file.num_entries<=o.max_buffer_keys) return Generate({file},o,temp);
  Generated g; g.planned=g.raw_rows=file.num_entries; const auto path=temp->Next();
  std::ofstream out(path,std::ios::binary); Need(bool(out),"cannot create merged run");
  bool have=false; uint64_t last=0;
  auto emit=[&](uint64_t key) { Need(!have || last<=key,"streamed merged keys decreased"); if(!have || last!=key) { WriteKey(out,key); ++g.perfile_distinct; } last=key;have=true; };
#ifndef VCOMP_REPLAY_ARCHIVED_LEGACY
  if(const auto* cdf=file.plr_model.DiscreteModel()) {
    Need(cdf->Count()==file.num_entries,"merged certificate count mismatch");
    auto cursor=cdf->NewCursor();uint64_t key=0;while(cursor.Next(&key))emit(key);
    Need(cursor.status().ok(),"merged CDF cursor failed");g.source="DiscreteCDF cursor (helper-equivalent)";
  } else
#endif
  {
    // Exact streaming equivalent of the legacy helper's segment walk,
    // increasing pass, then tail cap. This bound rules out uint64 wrap in the
    // helper's increasing pass; unsafe domains fail instead of guessing.
    Need(file.key_max<=kMax-file.num_entries,"legacy streaming overflow domain unsupported; use larger buffer for actual helper");
    const auto& segments=file.plr_model.Segments(); Need(!segments.empty(),"empty merged PLR");
    size_t segment=0; uint64_t previous_uncapped=0;
    for(uint64_t pos=0;pos<file.num_entries;++pos) {
      double position=static_cast<double>(pos);
      while(segment+1<segments.size()) {
        double pos_end=segments[segment].slope * static_cast<double>(segments[segment].key_end) + segments[segment].intercept;
        if(position<=pos_end)break; ++segment;
      }
      const auto& seg=segments[segment];uint64_t key;
      if(std::abs(seg.slope)<1e-15){Need(seg.key_start<=kMax-seg.key_end,"legacy streaming midpoint overflow unsupported");key=(seg.key_start+seg.key_end)/2;}
      else { double key_d=(position-seg.intercept)/seg.slope; key_d=std::max(key_d,static_cast<double>(seg.key_start));
        key_d=std::min(key_d,static_cast<double>(seg.key_end)); Need(std::isfinite(key_d) && key_d>=0 && key_d<18446744073709551616.0,"invalid legacy inverse value"); key=static_cast<uint64_t>(std::round(key_d)); }
      key=std::max(key,file.key_min);key=std::min(key,file.key_max);
      if(pos && key<=previous_uncapped)key=previous_uncapped+1;
      previous_uncapped=key;emit(std::min(key,file.key_max));
    }
    g.source="legacy streaming helper-equivalent (not SST writer)";
  }
  out.close();Need(bool(out),"merged stream write failed");g.runs.push_back(std::make_shared<Mapped>(path));return g;
}

Json Metrics(const Generated& generated,const Runs& truth,uint64_t n,const std::vector<File>& boundaries={},const Keys& witnesses={}) {
  const uint64_t u=CountUnion(generated.runs); Need(n>0,"empty oracle");
  struct Query { Wide edge; size_t index; };
  std::vector<Query> queries;std::vector<uint64_t> truth_at(boundaries.size()*2),generated_at(boundaries.size()*2);
  for(size_t i=0;i<boundaries.size();++i) { queries.push_back({boundaries[i].minimum,i*2});queries.push_back({Wide{boundaries[i].maximum}+1,i*2+1}); }
  std::sort(queries.begin(),queries.end(),[](const auto&a,const auto&b){return a.edge<b.edge;});size_t query=0;
  UnionCursor tc(truth),gc(generated.runs);uint64_t tk=0,gk=0,tcount=0,gcount=0,missing=0,invented=0,sup=0;Wide ks=0;
  size_t witness_index=0;uint64_t witness_present=0;
  bool ht=tc.Next(&tk),hg=gc.Next(&gk);
  while(ht||hg) {
    const uint64_t key=!ht?gk:!hg?tk:std::min(tk,gk);
    while(query<queries.size() && queries[query].edge<=Wide{key}) { truth_at[queries[query].index]=tcount;generated_at[queries[query].index]=gcount;++query; }
    const bool in_t=ht&&tk==key,in_g=hg&&gk==key;
    while(witness_index<witnesses.size() && witnesses[witness_index]<key)++witness_index;
    if(witness_index<witnesses.size() && witnesses[witness_index]==key){witness_present+=in_g;++witness_index;}
    if(in_t){++tcount;ht=tc.Next(&tk);}if(in_g){++gcount;hg=gc.Next(&gk);}
    missing+=in_t&&!in_g;invented+=in_g&&!in_t;
    sup=std::max(sup,tcount>gcount?tcount-gcount:gcount-tcount);
    Wide a=Wide{tcount}*u,b=Wide{gcount}*n;ks=std::max(ks,a>b?a-b:b-a);
  }
  while(query<queries.size()){truth_at[queries[query].index]=tcount;generated_at[queries[query].index]=gcount;++query;}
  Need(tcount==n && gcount==u,"metric count disagreement");Json result;
  result.Number("true_N",n);result.Number("generated_U",u);result.Number("planned_entries",generated.planned);
  result.Number("raw_helper_rows",generated.raw_rows);result.Number("perfile_distinct_sum",generated.perfile_distinct);
  result.Bool("valid_generation",generated.valid);
  result.Text("materializer_source",generated.source);result.Number("missing_original_keys",missing);result.Number("invented_keys",invented);
  result.Number("retained_input_witnesses",witnesses.size());result.Number("missing_input_witnesses",witnesses.size()-witness_present);
  result.Text("ks_exact_numerator",Decimal(ks));result.Text("ks_exact_denominator",Decimal(Wide{n}*u));
  if(u)result.Number("normalized_ecdf_ks",static_cast<long double>(ks)/(static_cast<long double>(n)*u));else result.fields["normalized_ecdf_ks"]="null";
  result.Number("sup_absolute_cumulative_count_difference",sup);result.Number("count_sup_over_truth_N",static_cast<long double>(sup)/n);
  std::vector<Json> ranges;
  for(size_t i=0;i<boundaries.size();++i){Json row;row.Number("actual_file",boundaries[i].number);row.Number("minimum",boundaries[i].minimum);row.Number("maximum",boundaries[i].maximum);
    row.Number("truth_before_min",truth_at[i*2]);row.Number("generated_before_min",generated_at[i*2]);
    row.Number("truth_through_max",truth_at[i*2+1]);row.Number("generated_through_max",generated_at[i*2+1]);
    row.Number("actual_file_unique_entries",boundaries[i].count);row.Number("truth_range_mass",truth_at[i*2+1]-truth_at[i*2]);
    row.Number("generated_range_mass",generated_at[i*2+1]-generated_at[i*2]);
    row.Number("before_min_count_error",static_cast<long double>(generated_at[i*2])-truth_at[i*2]);
    row.Number("through_max_count_error",static_cast<long double>(generated_at[i*2+1])-truth_at[i*2+1]);
    row.Number("range_mass_error_over_truth_N",(static_cast<long double>(generated_at[i*2+1]-generated_at[i*2])-(truth_at[i*2+1]-truth_at[i*2]))/n);
    ranges.push_back(std::move(row));}
  result.fields["actual_output_boundaries"]=Array(ranges);return result;
}
Json Metadata(const std::vector<r::VirtualSST>& files) {
  uint64_t segments=0,cells=0,global=0,range=0,buckets=0,modeled=0;
  for(const auto& f:files){segments+=f.plr_model.NumSegments();global+=f.kmv_sketch.samples.size();buckets+=f.kmv_ranges.size();
    for(const auto& b:f.kmv_ranges){range+=b.sketch.samples.size();
#ifndef VCOMP_REPLAY_ARCHIVED_LEGACY
      modeled+=b.entries_are_modeled;
#endif
    }
#ifndef VCOMP_REPLAY_ARCHIVED_LEGACY
    if(const auto* c=f.plr_model.DiscreteModel())cells+=c->Cells().size();
#endif
  }
  Json j;j.Number("files",files.size());j.Number("plr_segments",segments);j.Number("cdf_cells",cells);
  j.Number("global_samples",global);j.Number("range_samples",range);j.Number("range_buckets",buckets);j.Number("modeled_range_buckets",modeled);return j;
}

Json Replay(const Options& o) {
  const auto start=std::chrono::steady_clock::now();const auto cpu=std::clock();Json result;
  setenv("VCOMP_KMV_ENABLED","1",1);setenv("VCOMP_DISCRETE_CDF_ENABLED",o.variant=="legacy"?"0":"1",1);
  setenv("VCOMP_KMV_SAMPLES",std::to_string(o.samples).c_str(),1);setenv("VCOMP_KMV_RANGE_BUCKETS",std::to_string(o.buckets).c_str(),1);
#ifdef VCOMP_REPLAY_ARCHIVED_LEGACY
  Need(o.variant=="legacy" && o.samples==512 && o.buckets==8,"archived legacy build supports only legacy samples512/buckets8");
  result.Text("implementation","archived_pre_discrete_sources");
#else
  Need(r::VirtualSSTKMVSamples()==o.samples && r::VirtualSSTKMVRangeBuckets()==o.buckets,"effective sketch budget mismatch");
  result.Text("implementation",o.variant=="legacy"?"legacy_path_in_current_build":"discrete_path_in_current_build");
#endif
  const auto c=ReadCapture(o.capture);const auto input_runs=FileRuns(c.inputs),oracle=FileRuns(c.outputs);
  const uint64_t n=VerifySameUnion(input_runs,oracle);Need(n>0,"empty compaction union");Temp temporary(o.work);
  result.Text("capture",fs::absolute(o.capture).string());result.Text("job",c.scalars.at("job"));result.Text("variant",o.variant);
  result.Number("samples",o.samples);result.Number("buckets",o.buckets);result.Number("plr_error",o.error);
  result.Number("effective_samples",r::VirtualSSTKMVSamples());result.Number("effective_buckets",r::VirtualSSTKMVRangeBuckets());
  result.Number("total_input_unique_entries",c.input_sum);result.Number("actual_unique_entries",n);result.Number("max_buffer_keys",o.max_buffer_keys);
  result.Number("key_size",c.key_size);result.Number("value_size",c.value_size);result.Number("target_sst_size",c.target);result.Number("output_level",c.output_level);
  result.Bool("database_opened",false);result.Text("count_scope","distinct helper-generated IDs; not production SST-write counts");
  Generated before,after;std::vector<r::VirtualSST> files;std::vector<Json> input_metrics;
  Json timings;double phase_cpu=double(std::clock())/CLOCKS_PER_SEC;
  for(const auto& original:c.inputs) {
    Need(original.count<=o.max_buffer_keys,"input file exceeds --max-buffer-keys");Keys keys;keys.reserve(original.count);
    for(uint64_t i=0;i<original.count;++i)keys.push_back(original.keys->At(i));
    r::VirtualSST f{};f.key_min=original.minimum;f.key_max=original.maximum;f.num_entries=original.count;f.level=static_cast<int>(original.level);
    Need(Wide{f.num_entries}*(c.key_size+c.value_size)<=kMax,"modeled input size overflow");f.size_bytes=f.num_entries*(c.key_size+c.value_size);
    f.plr_model=r::GreedyPLRFit(keys,o.error);f.kmv_sketch=r::BuildKMVSketchFromSortedKeys(keys,o.samples);
    f.kmv_ranges=r::BuildKMVRangeSketchesFromSortedKeys(keys,o.samples,o.buckets);Keys().swap(keys);
    auto b=Generate({f},o,&temporary);Json row;row.Number("file_number",original.number);row.fields["before_certification"]=Metrics(b,{original.keys},original.count).Dump();
    before.planned+=b.planned;before.raw_rows+=b.raw_rows;before.perfile_distinct+=b.perfile_distinct;before.runs.insert(before.runs.end(),b.runs.begin(),b.runs.end());
#ifndef VCOMP_REPLAY_ARCHIVED_LEGACY
    if(o.variant=="discrete_certified") {const auto status=r::CertifyVirtualSST(&f);Need(status.ok(),"initial certification: "+status.ToString());}
#endif
    if(o.variant=="discrete_certified") {auto a=Generate({f},o,&temporary);row.fields["after_certification"]=Metrics(a,{original.keys},original.count).Dump();
      after.planned+=a.planned;after.raw_rows+=a.raw_rows;after.perfile_distinct+=a.perfile_distinct;after.runs.insert(after.runs.end(),a.runs.begin(),a.runs.end());}
    else row.fields["after_certification"]="null";
    files.push_back(std::move(f));input_metrics.push_back(std::move(row));
  }
  timings.Number("input_model_and_metrics_cpu_seconds",double(std::clock())/CLOCKS_PER_SEC-phase_cpu);Json stages;
  stages.fields["input_model_before_certification"]=Metrics(before,oracle,n).Dump();
  stages.fields["input_model_after_certification"]=o.variant=="discrete_certified"?Metrics(after,oracle,n).Dump():"null";
  result.fields["input_file_models"]=Array(input_metrics);Json metadata;metadata.fields["input"]=Metadata(files).Dump();
  std::vector<const r::VirtualSST*> pointers;for(const auto& f:files)pointers.push_back(&f);
  Keys witnesses,theta_keys;uint64_t theta=kMax;bool sketches_present=true,all_complete=true;
  for(const auto& f:files){theta=std::min(theta,f.kmv_sketch.theta_hash);all_complete=all_complete&&f.kmv_sketch.complete;
    sketches_present=sketches_present&&(!f.kmv_sketch.samples.empty()||f.kmv_sketch.complete);
    witnesses.push_back(f.key_min);witnesses.push_back(f.key_max);
    for(const auto& s:f.kmv_sketch.samples)witnesses.push_back(s.key);
    for(const auto& b:f.kmv_ranges)for(const auto& s:b.sketch.samples)witnesses.push_back(s.key);}
  for(const auto& f:files)for(const auto& s:f.kmv_sketch.samples)if(s.hash<=theta)theta_keys.push_back(s.key);
  std::sort(witnesses.begin(),witnesses.end());witnesses.erase(std::unique(witnesses.begin(),witnesses.end()),witnesses.end());
  std::sort(theta_keys.begin(),theta_keys.end());theta_keys.erase(std::unique(theta_keys.begin(),theta_keys.end()),theta_keys.end());
  const bool raw_available=sketches_present&&(all_complete||!theta_keys.empty());
  const uint64_t raw_estimate=raw_available?r::EstimateKMVUnionEntries(pointers,kMax,o.samples):0;
  const uint64_t requested=r::EstimateKMVUnionEntries(pointers,c.input_sum,o.samples);
  uint64_t chosen=0;phase_cpu=double(std::clock())/CLOCKS_PER_SEC;
#ifdef VCOMP_REPLAY_ARCHIVED_LEGACY
  auto merged=r::NWayMergeKMVRangeAware(pointers,&chosen,o.samples);
#else
  r::Status merge_status;auto merged=r::NWayMergeKMVRangeAware(pointers,&chosen,o.samples,&merge_status);Need(merge_status.ok(),"merge: "+merge_status.ToString());
#endif
  timings.Number("merge_model_cpu_seconds",double(std::clock())/CLOCKS_PER_SEC-phase_cpu);
  uint64_t minimum=kMax,maximum=0;for(const auto& f:files){minimum=std::min(minimum,f.key_min);maximum=std::max(maximum,f.key_max);}
  r::VirtualSST whole{};whole.plr_model=merged;whole.num_entries=chosen;whole.key_min=minimum;whole.key_max=maximum;whole.level=c.output_level;
  const auto gm=GenerateMerged(whole,o,&temporary);const auto merged_metrics=Metrics(gm,oracle,n,c.outputs,witnesses);
  stages.fields["merged"]=merged_metrics.Dump();metadata.fields["merged"]=Metadata({whole}).Dump();
  phase_cpu=double(std::clock())/CLOCKS_PER_SEC;
  const auto split=r::SplitIntoSSTs(merged,chosen,c.target,c.key_size+c.value_size,minimum,maximum,c.output_level,c.gp,&pointers,o.samples);
  timings.Number("split_model_cpu_seconds",double(std::clock())/CLOCKS_PER_SEC-phase_cpu);
  const auto gs=Generate(split,o,&temporary);const auto split_metrics=Metrics(gs,oracle,n,c.outputs,witnesses);
  stages.fields["split"]=split_metrics.Dump();metadata.fields["split"]=Metadata(split).Dump();
  uint64_t bad_capacity=0,overlaps=0,bad_bucket_sum=0;
  for(size_t i=0;i<split.size();++i){const auto& f=split[i];bad_capacity+=f.key_min>f.key_max || Wide{f.num_entries}>Wide{f.key_max}-f.key_min+1;
    for(size_t j=0;j<i;++j)overlaps+=std::max(f.key_min,split[j].key_min)<=std::min(f.key_max,split[j].key_max);
    uint64_t sum=0;for(const auto& b:f.kmv_ranges)sum+=b.num_entries;bad_bucket_sum+=sum!=f.num_entries;}
  const uint64_t split_u=CountUnion(gs.runs);bool same_selected=true;UnionCursor mc(gm.runs),sc(gs.runs);uint64_t mk=0,sk=0;
  for(;;){bool m=mc.Next(&mk),s=sc.Next(&sk);if(m!=s || (m && mk!=sk)){same_selected=false;break;}if(!m)break;}
  Json invariants;invariants.Bool("input_output_union_match",true);invariants.Bool("split_descriptor_sum_matches_D",gs.planned==chosen);
  invariants.Bool("generated_split_U_matches_D",split_u==chosen);invariants.Bool("split_matches_merged_selected_union",same_selected);
  invariants.Number("capacity_violations",bad_capacity);invariants.Number("sibling_range_overlaps",overlaps);invariants.Number("range_bucket_count_mismatches",bad_bucket_sum);
  const bool witness_retained=merged_metrics.fields.at("missing_input_witnesses")=="0" && split_metrics.fields.at("missing_input_witnesses")=="0";
  invariants.Bool("retained_witness_membership",witness_retained);
  const bool required=o.variant=="legacy" || (gm.valid && gs.valid && gs.planned==chosen && split_u==chosen && same_selected && witness_retained && !bad_capacity && !overlaps && !bad_bucket_sum);
  invariants.Bool("required_pass",required);Json dedup;dedup.Number("naive_entries",c.input_sum);dedup.Number("chosen_D",chosen);dedup.Number("true_N",n);
  dedup.Bool("raw_estimate_available",raw_available);if(raw_available)dedup.Number("raw_estimate_without_input_cap",raw_estimate);else dedup.fields["raw_estimate_without_input_cap"]="null";
  dedup.Number("requested_after_input_cap",requested);dedup.Bool("input_count_cap_applied",raw_available&&raw_estimate>requested);
  dedup.Bool("witness_capacity_projection_changed_D",chosen!=requested);dedup.Number("theta_hash",theta);dedup.Number("samples_at_common_theta",theta_keys.size());
  dedup.Bool("all_global_sketches_complete",all_complete);dedup.Number("theta_fraction",(static_cast<long double>(theta)+1)/(static_cast<long double>(kMax)+1));
  dedup.Number("relative_count_error",(static_cast<long double>(chosen)-n)/n);result.fields["dedup"]=dedup.Dump();
  result.fields["stages"]=stages.Dump();result.fields["metadata"]=metadata.Dump();result.fields["invariants"]=invariants.Dump();
  timings.Number("total_cpu_seconds",double(std::clock()-cpu)/CLOCKS_PER_SEC);timings.Number("wall_seconds",std::chrono::duration<double>(std::chrono::steady_clock::now()-start).count());
  result.fields["timings"]=timings.Dump();result.Text("status",required?"ok":"error");if(!required)result.Text("error","required discrete count/range invariant failed");return result;
}
}  // namespace

int main(int argc,char** argv) {
  Options options;Json result;int exit_code=0;
  // Recover an output path even if another CLI argument is invalid.
  for(int i=1;i<argc;++i){std::string arg=argv[i];if(arg=="--output" && i+1<argc)options.output=argv[i+1];else if(arg.rfind("--output=",0)==0)options.output=arg.substr(9);}
  std::error_code output_error;
  const bool output_exists=!options.output.empty() && fs::exists(options.output,output_error);
  if(output_error) {std::cerr<<"cannot inspect output path: "<<output_error.message()<<'\n';return 2;}
  if(output_exists) {std::cerr<<"output already exists; use a fresh result path\n";return 2;}
  try {options=Parse(argc,argv);result=Replay(options);if(result.fields.at("status")!=Quote("ok"))exit_code=1;}
  catch(const std::exception& error){result.Text("status","error");result.Text("error",error.what());exit_code=1;}
  try {Need(!options.output.empty(),"no --output path");if(!options.output.parent_path().empty())fs::create_directories(options.output.parent_path());
    std::ofstream output(options.output);Need(bool(output),"cannot write result JSON");output<<result.Dump()<<'\n';output.close();Need(bool(output),"result JSON write failed");}
  catch(const std::exception& error){std::cerr<<error.what()<<'\n'<<result.Dump()<<'\n';return 2;}
  if(exit_code)std::cerr<<result.Dump()<<'\n';return exit_code;
}
